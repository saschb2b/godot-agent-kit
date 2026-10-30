"""Multi-process scenarios: an external server plus N headless Godot clients of the project.

A scenario with a "topology" key starts the server command, waits until a line of its output
matches "ready" (whose named group "port" becomes {port}), starts one headless Godot process per
client with that client's arguments and steps, and waits for all of them under one global
timeout. Every process gets its own log; the clients' results are merged into one record. With
"compare_hashes" the "HASH <tick> <hex>" lines the clients print must agree at every tick two
or more of them hashed, and the first tick where they do not is reported.

Every process starts in its own session (a new process group on Windows) and is stopped with its
whole group or tree, so a server that forks workers leaves no orphans behind.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import runner
from .env import MAX_RSS_GB, GodotAgentError, admit, rss_gb

PLACEHOLDERS = ("{port}", "{client}", "{clients}", "{out}", "{project}", "{python}")
DEFAULT_MAX_FPS = 60
STOP_GRACE_S = 5.0

_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


# ── Validation ──

def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate(topo: object, origin: str) -> None:
    where = f"{origin}: topology"
    if not isinstance(topo, dict):
        raise GodotAgentError(f"{where} must be an object with 'server' and 'clients'")
    server = topo.get("server")
    if server is not None:
        if not isinstance(server, dict):
            raise GodotAgentError(f"{where}.server must be an object")
        if not _strings(server.get("cmd")) or not server["cmd"]:
            raise GodotAgentError(f"{where}.server.cmd must be a non-empty list of strings")
        if not isinstance(server.get("ready"), str) or not server["ready"]:
            raise GodotAgentError(f"{where}.server.ready must be a regex matched against the server's output, "
                                  "e.g. \"listening.*port (?P<port>\\\\d+)\"")
        try:
            re.compile(server["ready"])
        except re.error as exc:
            raise GodotAgentError(f"{where}.server.ready is not a valid regex: {exc}") from exc
        if "cwd" in server and not isinstance(server["cwd"], str):
            raise GodotAgentError(f"{where}.server.cwd must be a path")
        if "env" in server and not isinstance(server["env"], dict):
            raise GodotAgentError(f"{where}.server.env must be an object")
    clients = topo.get("clients")
    if not isinstance(clients, list) or not clients:
        raise GodotAgentError(f"{where}.clients must be a non-empty list of objects")
    for k, client in enumerate(clients, 1):
        if not isinstance(client, dict):
            raise GodotAgentError(f"{where}.clients[{k}] must be an object")
        if "args" in client and not _strings(client["args"]):
            raise GodotAgentError(f"{where}.clients[{k}].args must be a list of strings")
        if "scenario" in client and not isinstance(client["scenario"], str):
            raise GodotAgentError(f"{where}.clients[{k}].scenario must be a path to a scenario or steps file")
        if "steps" in client and not isinstance(client["steps"], list):
            raise GodotAgentError(f"{where}.clients[{k}].steps must be a list")
    if "compare_hashes" in topo and not isinstance(topo["compare_hashes"], bool):
        raise GodotAgentError(f"{where}.compare_hashes must be true or false")
    timeout = topo.get("timeout", 1)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise GodotAgentError(f"{where}.timeout must be a number of seconds > 0")
    max_fps = topo.get("max_fps", 0)
    if isinstance(max_fps, bool) or not isinstance(max_fps, int) or max_fps < 0:
        raise GodotAgentError(f"{where}.max_fps must be a whole number >= 0 (0: unpaced)")


# ── Processes ──

def spawn(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Starts a process in its own session (POSIX) or process group (Windows), tracked for cleanup."""
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    admit()
    proc = subprocess.Popen(cmd, **kwargs)
    with _LIVE_LOCK:
        _LIVE.add(proc)
    return proc


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop(proc: subprocess.Popen, grace: float = STOP_GRACE_S) -> None:
    """Stops a spawned process and everything it started: SIGTERM to its process group, SIGKILL
    after `grace` seconds, then SIGKILL to whatever is left in the group (orphaned children).
    On Windows `taskkill /T /F` ends the process tree."""
    try:
        if os.name == "nt":
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return
        if proc.poll() is None:
            _signal_group(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                _signal_group(proc.pid, signal.SIGKILL)
                proc.wait()
        # Children it started outlive it in its process group; nothing there is wanted any more.
        _signal_group(proc.pid, signal.SIGKILL)
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(proc)


def kill_live() -> None:
    """Stops every process a topology started that is still tracked (Ctrl-C, SIGTERM, exit)."""
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for proc in procs:
        stop(proc, grace=1.0)


atexit.register(kill_live)


def _fill(value, values: dict[str, str]):
    """Replaces the placeholders in every string of a JSON value."""
    if isinstance(value, str):
        for key, replacement in values.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    return value


def _split_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Engine options before "--", the game's own arguments after it (all of them without "--")."""
    if "--" in args:
        i = args.index("--")
        return args[:i], args[i + 1:]
    return [], args


class _Server:
    def __init__(self, cfg: dict, project: Path, out: Path, values: dict[str, str]):
        self.cmd = [_fill(a, values) for a in cfg["cmd"]]
        cwd = Path(_fill(cfg.get("cwd", "."), values)).expanduser()
        self.cwd = cwd if cwd.is_absolute() else project / cwd
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        env.update({str(k): _fill(str(v), values) for k, v in cfg.get("env", {}).items()})
        self.pattern = re.compile(cfg["ready"])
        self.ready = threading.Event()
        self.groups: dict[str, str] = {}
        self.ready_line = ""
        self.stopped = False
        self.log_path = out / "server.log"
        self._log = open(self.log_path, "wb")
        try:
            self.proc = spawn(self.cmd, cwd=self.cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError:
            self._log.close()
            raise
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            self._log.write(raw)
            self._log.flush()
            if not self.ready.is_set():
                line = runner.ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\r\n")
                if match := self.pattern.search(line):
                    self.groups = {k: v for k, v in match.groupdict().items() if v is not None}
                    self.ready_line = line.strip()
                    self.ready.set()
        self.proc.stdout.close()

    def wait_ready(self, deadline: float) -> str | None:
        """None when ready, else why not."""
        while not self.ready.is_set():
            if self.proc.poll() is not None:
                self._thread.join(timeout=2)  # it may have printed the line just before exiting
                if self.ready.is_set():
                    break
                return f"server exited with {self.proc.returncode} before its output matched ready"
            if time.monotonic() >= deadline:
                return "timed out waiting for the server's output to match ready"
            time.sleep(0.02)
        return None

    def close(self) -> None:
        self.stopped = self.proc.poll() is None
        stop(self.proc)
        self._thread.join(timeout=5)
        self._log.close()

    def tail(self, lines: int = 15) -> list[str]:
        try:
            return self.log_path.read_text(errors="replace").strip().splitlines()[-lines:]
        except OSError:
            return []


@dataclass
class _Client:
    job: runner.Job
    args: list[str]
    proc: subprocess.Popen | None = None
    data_home: str = ""
    log: object = None
    killed: bool = False
    record: dict = field(default_factory=dict)


# ── Scenario of each client ──

SHARED_SKIP = ("topology", "name", "description", "compare_hashes", "deterministic")


def _resolve_file(raw: str, scenario_file: Path, project: Path) -> Path:
    if raw.startswith("res://"):
        return project / raw[len("res://"):]
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    beside = scenario_file.parent / path
    return beside if beside.exists() else project / path


def _client_scenario(job: runner.Job, client: dict, k: int, project: Path) -> dict:
    """The topology scenario's own keys and steps, overlaid by the client's scenario file and keys."""
    scenario = {key: value for key, value in job.scenario.items() if key not in SHARED_SKIP}
    if client.get("scenario"):
        path = _resolve_file(client["scenario"], job.scenario_file, project)
        try:
            data = json.loads(path.read_text())
        except OSError as exc:
            raise GodotAgentError(f"{job.name}: client {k} scenario {client['scenario']}: {exc}") from exc
        except ValueError as exc:
            raise GodotAgentError(f"{job.name}: client {k} scenario {path}: invalid JSON: {exc}") from exc
        scenario.update({"steps": data} if isinstance(data, list) else data)
    scenario.update({key: value for key, value in client.items() if key not in ("args", "scenario")})
    scenario.pop("topology", None)
    scenario["name"] = f"{job.name}.client{k}"
    return scenario


# ── Running ──

def run(godot: str, project: Path, job: runner.Job, timeout: float, renderer: str | None = None) -> dict:
    topo = job.scenario["topology"]
    out = job.out_dir
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    limit = float(topo.get("timeout", timeout))
    deadline = started + limit
    max_fps = int(topo.get("max_fps", DEFAULT_MAX_FPS))
    clients_cfg: list[dict] = topo["clients"]
    values = {"{out}": str(out), "{project}": str(project), "{python}": sys.executable,
              "{clients}": str(len(clients_cfg))}
    compare = bool(job.scenario.get("compare_hashes", topo.get("compare_hashes", False)))

    problems: list[str] = []
    server: _Server | None = None
    clients: list[_Client] = []
    timed_out = False
    try:
        if topo.get("server"):
            try:
                server = _Server(topo["server"], project, out, values)
            except OSError as exc:
                problems.append(f"server did not start: {exc}")
            else:
                if why := server.wait_ready(deadline):
                    problems.append(why)
                    timed_out = why.startswith("timed out")
        if not problems:
            port = server.groups.get("port") if server else None
            for k, cfg in enumerate(clients_cfg, 1):
                local = dict(values, **{"{client}": str(k)})
                if port is not None:
                    local["{port}"] = str(port)
                scenario = _fill(_client_scenario(job, cfg, k, project), local)
                scenario.setdefault("max_fps", max_fps)
                args = [_fill(a, local) for a in cfg.get("args", [])]
                if any("{port}" in a for a in args) or "{port}" in json.dumps(scenario):
                    problems.append("a client uses {port}, but the server's ready pattern captured no "
                                    "(?P<port>\\d+) group")
                    break
                client_dir = out / f"client{k}"
                client_dir.mkdir(parents=True, exist_ok=True)
                file = client_dir / "scenario.json"
                file.write_text(json.dumps(runner.validate_scenario(scenario, f"{job.name} client {k}"), indent=2))
                clients.append(_Client(runner.Job(f"client{k}", scenario, file, client_dir, False), args))
        if not problems:
            for client in clients:
                engine_args, user_args = _split_args(client.args)
                cmd, env, client.data_home, _ = runner.launch_command(
                    godot, project, client.job, None, False, renderer, engine_args, user_args)
                client.log = open(client.job.out_dir / "godot.log", "wb")
                client.proc = spawn(cmd, env=env, stdin=subprocess.DEVNULL, stdout=client.log,
                                    stderr=subprocess.STDOUT)
            while any(c.proc.poll() is None for c in clients):
                if time.monotonic() >= deadline:
                    timed_out = True
                    for c in clients:
                        c.killed = c.proc.poll() is None
                    break
                for k, c in enumerate(clients, 1):
                    held = rss_gb(c.proc.pid) if c.proc.poll() is None else None
                    if held is not None and held > MAX_RSS_GB:
                        c.killed = True
                        problems.append(f"client {k} stopped: it held {held:.1f} GB, over the {MAX_RSS_GB:g} GB cap "
                                        "(GODOT_AGENT_MAX_RSS_GB)")
                        stop(c.proc, grace=0.5)
                held = rss_gb(server.proc.pid) if server and server.proc.poll() is None else None
                if held is not None and held > MAX_RSS_GB:
                    problems.append(f"server stopped: it held {held:.1f} GB, over the {MAX_RSS_GB:g} GB cap")
                    stop(server.proc, grace=0.5)
                time.sleep(0.05)
            if server and server.proc.poll() not in (None, 0):
                problems.append(f"server exited with {server.proc.returncode} while the clients ran")
    finally:
        for client in clients:
            if client.proc:
                stop(client.proc, grace=1.0)
            if client.log:
                client.log.close()
            if client.data_home:
                shutil.rmtree(client.data_home, ignore_errors=True)
        if server:
            server.close()

    return _merge(job, out, started, limit, server, clients, problems, timed_out, compare)


def compare_hash_streams(streams: dict[str, dict[int, str]]) -> dict:
    """Ticks hashed by two or more processes must carry one hash. Reports the first that does not."""
    compared = 0
    last_agreed = None
    first = None
    for tick in sorted(set().union(*streams.values()) if streams else []):
        seen = {name: stream[tick] for name, stream in streams.items() if tick in stream}
        if len(seen) < 2:
            continue
        compared += 1
        if len(set(seen.values())) > 1:
            first = first or {"tick": tick, "hashes": seen}
        elif first is None:
            last_agreed = tick
    return {"compared_ticks": compared, "last_agreed_tick": last_agreed, "first_divergence": first,
            "counts": {name: len(stream) for name, stream in streams.items()}}


def _merge(job: runner.Job, out: Path, started: float, limit: float, server: _Server | None,
           clients: list[_Client], problems: list[str], timed_out: bool, compare: bool) -> dict:
    streams: dict[str, dict[int, str]] = {}
    for client in clients:
        if not client.proc:
            continue
        log_text = (client.job.out_dir / "godot.log").read_text(errors="replace")
        reason = f"still running at the topology timeout ({limit:g}s); stopped" if client.killed else None
        client.record = runner.make_record(client.job, client.proc.returncode, log_text, False, started, reason)
        client.record.update(pid=client.proc.pid, args=client.args)
        streams[client.job.name] = runner.hash_stream(log_text)

    records = [c.record for c in clients if c.record]
    hashes = compare_hash_streams(streams) if compare or any(streams.values()) else None
    reasons = list(problems)
    if timed_out and not problems:
        running = [c.job.name for c in clients if c.killed]
        reasons.append(f"timed out after {limit:g}s" + (f" ({', '.join(running)} still running)" if running else ""))
    if compare and hashes is not None:
        if divergence := hashes["first_divergence"]:
            shown = ", ".join(f"{name} {value[:16]}" for name, value in divergence["hashes"].items())
            reasons.append(f"hashes diverge at tick {divergence['tick']}: {shown}")
        elif hashes["compared_ticks"] == 0:
            reasons.append("compare_hashes: no tick was hashed by two clients "
                           "(clients print 'HASH <tick> <hex>' lines to stdout)")
    for record in records:
        if not record["passed"]:
            reasons.append(f"{record['name']}: {record.get('reason', '')}")
    if not records and not reasons:
        reasons.append("no client ran")

    def tagged(key: str) -> list:
        return [{**item, "client": r["name"]} for r in records for item in r.get(key, [])]

    passed = not reasons and bool(records)
    server_info = None
    if server:
        server_info = {"cmd": server.cmd, "cwd": str(server.cwd), "pid": server.proc.pid,
                       "ready": server.ready.is_set(), "ready_line": server.ready_line,
                       "port": server.groups.get("port"), "exit_code": server.proc.returncode,
                       "stopped_by_kit": server.stopped,
                       "log": str(server.log_path)}
        if not server.ready.is_set():
            server_info["log_tail"] = server.tail()
    return {
        "name": job.name, "out_dir": str(out), "rendered": False,
        "wall_s": round(time.monotonic() - started, 2), "exit_code": 0 if passed else 1,
        "passed": passed, "reason": reasons[0] if reasons else "",
        "frames": max((r.get("frames", 0) for r in records), default=0),
        "failures": tagged("failures"), "failed_assertions": tagged("failed_assertions"),
        "assertions": sum(r.get("assertions", 0) for r in records),
        "expression_errors": tagged("expression_errors"), "errors": tagged("errors"),
        "warnings": sum(r.get("warnings", 0) for r in records), "shutdown": [],
        "final_watches": {r["name"]: r.get("final_watches", {}) for r in records},
        "vars": {}, "telemetry": {}, "captures": [],
        "skipped_captures": sum(r.get("skipped_captures", 0) for r in records),
        "server": server_info, "clients": records, "hashes": hashes, "compare_hashes": compare,
    }


def format_lines(record: dict) -> list[str]:
    lines = []
    server = record.get("server")
    if server:
        state = f"ready on port {server['port']}" if server.get("ready") and server.get("port") else (
            "ready" if server.get("ready") else "NOT READY")
        ended = "stopped after the clients" if server.get("stopped_by_kit") else f"exited {server.get('exit_code')}"
        lines.append(f"        server: {state} · {ended} · {server.get('log')}")
        for line in server.get("log_tail", [])[-6:]:
            lines.append(f"        | {line}")
    for client in record.get("clients", []):
        status = "PASS" if client["passed"] else "FAIL"
        detail = f"{client.get('frames', 0)} frames"
        if client.get("hashes"):
            detail += f", {client['hashes']} hashes"
        lines.append(f"        {client['name']}: {status} · {detail} · {client['out_dir']}/godot.log")
        if not client["passed"]:
            lines.append(f"          reason: {client.get('reason', '')}")
            for tail in client.get("log_tail", [])[-4:]:
                lines.append(f"          | {tail}")
    hashes = record.get("hashes")
    if hashes:
        if hashes.get("first_divergence"):
            divergence = hashes["first_divergence"]
            lines.append(f"        hashes: first divergence at tick {divergence['tick']} "
                         f"(last agreed tick {hashes.get('last_agreed_tick')}):")
            lines += [f"          {name}: {value}" for name, value in divergence["hashes"].items()]
        else:
            lines.append(f"        hashes: {hashes['compared_ticks']} shared ticks agree"
                         + ("" if record.get("compare_hashes") else " (not compared: compare_hashes is off)"))
    return lines
