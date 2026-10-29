"""Runs scenarios in parallel Godot processes and turns their evidence into a summary."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import sheet, topology
from .env import (CACHE_DIR, RUNTIME_DIR, GodotAgentError, find_godot, project_name,
                  refresh_class_cache, resolve_project, virtual_display)

STEP_KINDS = ("wait", "wait_until", "wait_signal", "press", "release", "tap", "key", "click", "hover",
              "eval", "set", "scene", "assert", "capture", "observe", "log", "repeat", "set_ticks_per_frame")
DEFAULT_SCENARIO_DIRS = ("tests/scenarios", "scenarios", ".godot-agent/scenarios")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOG_RE = re.compile(r"^(?:USER )?(?:SCRIPT )?(ERROR|WARNING): (.*)$")
AT_RE = re.compile(r"^\s+at: (.*)$")
# Warnings a virtual display or software renderer always prints; not the game's fault.
LOG_NOISE = ("wd.xic", "V-Sync", "MESA", "DRI3", "XInput", "xim server")
KEEP_RUNS = 30
# "HASH <tick> <hex>" lines a game prints to stdout: its state hash at a simulation tick.
HASH_RE = re.compile(r"^HASH (\d+) ([0-9A-Fa-f]+)\s*$")
RESULT_MARKER = "[godot-agent] result written"
# A rendered run (llvmpipe under Xvfb) holds about 2 GB. Rendered runs from every
# godot-agent process on the machine share a few slots, so parallel agents cannot
# run it out of memory together. GODOT_AGENT_RENDER_SLOTS overrides the count.
RENDER_GB = 2.0


@dataclass
class Job:
    name: str
    scenario: dict
    scenario_file: Path
    out_dir: Path
    render: bool


# ── Scenario files ──

def collect_scenarios(project: Path, paths: list[str] | None, name_filter: str | None = None) -> list[Path]:
    if not paths:
        paths = [d for d in DEFAULT_SCENARIO_DIRS if (project / d).is_dir()]
        if not paths:
            raise GodotAgentError(f"No scenarios given and none of {', '.join(DEFAULT_SCENARIO_DIRS)} exist in {project}")
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_absolute() and not path.exists():
            path = project / raw
        if path.is_dir():
            found += sorted(path.rglob("*.json"))
        elif path.is_file():
            found.append(path.resolve())
        else:
            raise GodotAgentError(f"Scenario not found: {raw}")
    if name_filter:
        found = [p for p in found if name_filter in p.stem]
    if not found:
        raise GodotAgentError("No scenario files matched")
    return found


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_scenario(scenario: object, origin: str) -> dict:
    """Catches malformed scenarios before spending a Godot launch on them."""
    if not isinstance(scenario, dict):
        raise GodotAgentError(f"{origin}: scenario must be a JSON object")
    steps = scenario.get("steps", [])
    if not isinstance(steps, list):
        raise GodotAgentError(f"{origin}: 'steps' must be a list")
    if "ticks_per_frame" in scenario and not _count(scenario["ticks_per_frame"]):
        raise GodotAgentError(f"{origin}: 'ticks_per_frame' must be a whole number >= 0 (default 1)")
    if "max_fps" in scenario and not _count(scenario["max_fps"]):
        raise GodotAgentError(f"{origin}: 'max_fps' must be a whole number >= 0 (0: as fast as possible)")
    for key in ("deterministic", "compare_hashes"):
        if key in scenario and not isinstance(scenario[key], bool):
            raise GodotAgentError(f"{origin}: '{key}' must be true or false")
    if "topology" in scenario:
        topology.validate(scenario["topology"], origin)

    def check(step_list: list, prefix: str) -> None:
        for i, step in enumerate(step_list, 1):
            where = f"{origin} step {prefix}{i}"
            if not isinstance(step, dict):
                raise GodotAgentError(f"{where}: must be an object")
            kinds = [k for k in STEP_KINDS if k in step]
            if not kinds:
                raise GodotAgentError(f"{where}: no step type; use one of {', '.join(STEP_KINDS)} (got {list(step)})")
            if kinds[0] == "repeat":
                check(step.get("steps", []), f"{prefix}{i}.")
            if kinds[0] == "set_ticks_per_frame" and not _count(step["set_ticks_per_frame"]):
                raise GodotAgentError(f"{where}: 'set_ticks_per_frame' must be a whole number >= 0")

    check(steps, "")
    return scenario


def load_scenario(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise GodotAgentError(f"{path}: invalid JSON: {exc}") from exc
    return validate_scenario(data, path.name)


def needs_render(scenario: dict) -> bool:
    if "topology" in scenario:
        return False  # topology clients always run headless
    if int(scenario.get("capture_every", 0) or 0) > 0:
        return True

    def has_capture(steps: list) -> bool:
        return any("capture" in s or ("repeat" in s and has_capture(s.get("steps", []))) for s in steps
                   if isinstance(s, dict))

    return has_capture(scenario.get("steps", []))


def is_deterministic(scenario: dict) -> bool:
    """Whether --determinism compares two runs. Topologies talk over real sockets, so they are
    compared only when the scenario says its clients are deterministic by themselves."""
    return bool(scenario.get("deterministic", "topology" not in scenario))


def kit_warnings(scenario: dict, visible: bool = False) -> list[str]:
    """Settings that run but probably do not do what the scenario meant."""
    warnings = []
    try:
        time_scale = float(scenario.get("time_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        time_scale = 1.0
    if time_scale > 1 and not visible:
        warnings.append(
            f"time_scale {time_scale:g} under --fixed-fps keeps one physics tick per frame and makes each tick "
            f"{time_scale:g}/60 s long (max_physics_steps_per_frame does not apply), so a fixed-step simulation is "
            "not tick-exact; fast-forward with \"ticks_per_frame\" instead")
    return warnings


def hash_stream(log_text: str) -> dict[int, str]:
    """The HASH lines a process printed, by tick (lowercase hex; a later line for a tick wins)."""
    stream: dict[int, str] = {}
    for line in ANSI_RE.sub("", log_text).splitlines():
        if match := HASH_RE.match(line.strip()):
            stream[int(match.group(1))] = match.group(2).lower()
    return stream


# ── Godot output ──

def parse_log(text: str) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []
    lines = ANSI_RE.sub("", text).splitlines()
    for i, line in enumerate(lines):
        match = LOG_RE.match(line)
        if not match:
            continue
        entry = {"message": match.group(2).strip()}
        if i + 1 < len(lines) and (at := AT_RE.match(lines[i + 1])):
            entry["at"] = at.group(1).strip()
        if match.group(1) == "ERROR":
            errors.append(entry)
        elif not any(noise in entry["message"] for noise in LOG_NOISE):
            warnings.append(entry)
    return errors, warnings


def _same_error(log_message: str, known: set[str]) -> bool:
    return any(k and (k in log_message or log_message in k) for k in known)


def _ignored(message: str, patterns: list) -> bool:
    return any(re.search(str(p), message) for p in patterns)


# ── Running ──

def launch_command(godot: str, project: Path, job: Job, display: str | None, visible: bool,
                   renderer: str | None = None, engine_args: list[str] | None = None,
                   user_args: list[str] | None = None) -> tuple[list[str], dict, str, bool]:
    """The Godot command line, environment, private data folder and whether it renders."""
    # A private user:// directory per run: saves cannot leak between runs or
    # touch the developer's real save files, and parallel runs cannot collide.
    data_home = tempfile.mkdtemp(prefix="godot-agent-data-")
    env = dict(os.environ, XDG_DATA_HOME=data_home)
    cmd = [godot]
    rendered = job.render and (display is not None or visible)
    if not rendered:
        cmd.append("--headless")
    elif display:
        env["DISPLAY"] = display
        env.pop("WAYLAND_DISPLAY", None)
        # Top-left of a large virtual screen: the X pointer rests at the screen's
        # centre, outside the window, so no real mouse events reach the game.
        cmd += ["--display-driver", "x11", "--audio-driver", "Dummy", "--position", "0,0"]
    if visible:
        env["GODOT_AGENT_VISIBLE"] = "1"
    if renderer:
        cmd += ["--rendering-method", renderer]
    cmd += list(engine_args or [])
    cmd += ["--path", str(project)]
    if not visible:
        # Fixed timestep: same inputs give the same frames, as fast as the CPU allows.
        cmd += ["--fixed-fps", "60", "--disable-vsync"]
    cmd += ["-s", str(RUNTIME_DIR / "boot.gd"), "--",
            f"--ga-scenario={job.scenario_file}", f"--ga-out={job.out_dir}", *(user_args or [])]
    return cmd, env, data_home, rendered


def run_job(godot: str, project: Path, job: Job, display: str | None, visible: bool, timeout: int,
            renderer: str | None = None) -> dict:
    job.out_dir.mkdir(parents=True, exist_ok=True)
    cmd, env, data_home, rendered = launch_command(godot, project, job, display, visible, renderer)
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env)
        returncode, output = proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        returncode, output, timed_out = -1, exc.output or b"", True
    finally:
        shutil.rmtree(data_home, ignore_errors=True)
    log_text = output.decode("utf-8", errors="replace")
    (job.out_dir / "godot.log").write_text(log_text)
    return make_record(job, returncode, log_text, rendered, started,
                       f"timed out after {timeout}s" if timed_out else None)


def make_record(job: Job, returncode: int, log_text: str, rendered: bool, started: float,
                timeout_reason: str | None = None) -> dict:
    """One process's evidence: its result.json, plus engine errors from its log."""
    # Leak reports printed while the engine shuts down come after the marker.
    run_text, _, shutdown_text = log_text.partition(RESULT_MARKER)
    log_errors, log_warnings = parse_log(run_text)
    shutdown_errors, _ = parse_log(shutdown_text)

    record: dict = {"name": job.name, "out_dir": str(job.out_dir), "rendered": rendered,
                    "wall_s": round(time.monotonic() - started, 2), "exit_code": returncode}
    result_path = job.out_dir / "result.json"
    if not result_path.exists():
        record.update(passed=False, frames=0, log_errors=log_errors[:10],
                      reason=timeout_reason or f"Godot exited with {returncode} before writing a result",
                      log_tail=log_text.strip().splitlines()[-15:])
        if hashes := hash_stream(log_text):
            record["hashes"] = len(hashes)
        return record

    result = json.loads(result_path.read_text())
    known = {str(e.get("message", "")) for e in result.get("errors", [])}
    ignore = job.scenario.get("ignore_errors", [])
    # Errors printed before the in-game logger existed (autoload or parse errors).
    extra = [e for e in log_errors if not _same_error(e["message"], known) and not _ignored(e["message"], ignore)]
    fail_on_errors = bool(job.scenario.get("fail_on_errors", True))
    passed = bool(result.get("passed")) and returncode == 0 and not (fail_on_errors and extra)
    reason = result.get("reason") or ""
    if not passed and not reason:
        reason = f"engine error: {extra[0]['message']}" if extra else f"exit code {returncode}"

    captures = [c for c in result.get("captures", []) if c.get("path")]
    record.update(
        passed=passed, reason=reason, frames=result.get("frames", 0),
        failures=result.get("failures", []),
        failed_assertions=[a for a in result.get("assertions", []) if not a.get("ok")],
        assertions=len(result.get("assertions", [])),
        expression_errors=result.get("expression_errors", []),
        errors=[e for e in result.get("errors", []) if e.get("type") != "warning"] + extra,
        warnings=len(log_warnings),
        shutdown=[e["message"] for e in shutdown_errors][:5],
        final_watches=result.get("final_watches", {}),
        vars=result.get("vars", {}),
        telemetry=result.get("telemetry", {}),
        captures=[str(job.out_dir / c["path"]) for c in captures],
        skipped_captures=sum(1 for c in result.get("captures", []) if c.get("skipped")),
    )
    if hashes := hash_stream(log_text):
        record["hashes"] = len(hashes)
    if captures:
        sheet_path = sheet.contact_sheet([(job.out_dir / c["path"], f'{c["label"]} · f{c["frame"]}') for c in captures],
                                         job.out_dir / "contact_sheet.png")
        if sheet_path:
            record["contact_sheet"] = str(sheet_path)
    return record


def _meminfo_gb(key: str) -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(key + ":"):
                return int(line.split()[1]) / 1048576
    except (OSError, ValueError, IndexError):
        pass
    return None


def render_slots() -> int:
    if os.environ.get("GODOT_AGENT_RENDER_SLOTS"):
        return max(1, int(os.environ["GODOT_AGENT_RENDER_SLOTS"]))
    total = _meminfo_gb("MemTotal")
    return max(1, int(total // 6)) if total else 4


@contextlib.contextmanager
def render_slot():
    """Holds one of the machine-wide render slots while a rendered run is alive.
    Slot 0 always runs, so work never stalls; the others also wait for free memory."""
    import fcntl
    folder = CACHE_DIR / "render-slots"
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        for i in range(render_slots()):
            handle = open(folder / f"slot-{i}.lock", "a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                continue
            free = _meminfo_gb("MemAvailable")
            if i == 0 or free is None or free >= RENDER_GB * 1.5:
                try:
                    yield i
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    handle.close()
                return
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        time.sleep(1.0)


def run_scenarios(project: str | os.PathLike, paths: list[str] | None = None, inline: dict | None = None,
                  jobs: int = 4, determinism: bool = False, headless: bool = False, visible: bool = False,
                  out: str | None = None, timeout: int = 180, refresh: bool = True,
                  name_filter: str | None = None, renderer: str | None = None) -> dict:
    godot = find_godot()
    project_path = resolve_project(project)
    started = time.monotonic()
    notes: list[str] = []
    if refresh and (note := refresh_class_cache(godot, project_path)):
        notes.append(note)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    runs_root = CACHE_DIR / "runs" / project_name(project_path)
    run_dir = Path(out).expanduser().resolve() if out else runs_root / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    scenarios: list[tuple[Path, dict]] = []
    if inline is not None:
        scenario = validate_scenario(inline, "inline scenario")
        file = run_dir / f"{scenario.get('name') or 'inline'}.scenario.json"
        file.write_text(json.dumps(scenario, indent=2))
        scenarios.append((file, scenario))
    if paths or inline is None:
        scenarios += [(p, load_scenario(p)) for p in collect_scenarios(project_path, paths, name_filter)]

    # One group per scenario: two runs to compare under --determinism, else one.
    groups: list[list[Job]] = []
    seen: set[str] = set()
    for file, scenario in scenarios:
        name = str(scenario.get("name") or file.stem.replace(".scenario", ""))
        while name in seen:
            name += "_"
        seen.add(name)
        render = needs_render(scenario) and not headless
        attempts = ("a", "b") if determinism and is_deterministic(scenario) else ("",)
        groups.append([Job(name, scenario, file, run_dir / name / attempt if attempt else run_dir / name, render)
                       for attempt in attempts])
    job_list = [job for group in groups for job in group]

    wants_render = any(j.render for j in job_list)
    display_ctx = virtual_display() if wants_render and not visible else contextlib.nullcontext(None)
    with display_ctx as display:
        if wants_render and not visible and display is None:
            notes.append("Xvfb not installed: ran headless, captures skipped (run install.sh)")
        def run_one(job: Job) -> dict:
            if "topology" in job.scenario:
                record = topology.run(godot, project_path, job, timeout, renderer)
            else:
                limit = render_slot() if job.render and display is not None else contextlib.nullcontext()
                with limit:
                    record = run_job(godot, project_path, job, display, visible, timeout, renderer)
            if warnings := kit_warnings(job.scenario, visible):
                record["notes"] = warnings
            return record

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            try:
                runs = list(pool.map(run_one, job_list))
            except BaseException:
                # Ctrl-C or SIGTERM: topology processes live in their own sessions, so stop them here.
                topology.kill_live()
                raise

    records = []
    done = iter(runs)
    for group in groups:
        if len(group) == 2:
            records.append(_merge_determinism(next(done), next(done)))
            continue
        record = next(done)
        if determinism:
            record["deterministic"] = None
            record["determinism"] = ("not compared: \"deterministic\": false" if "deterministic" in group[0].scenario
                                     else "not compared: a topology talks over real sockets "
                                          "(set \"deterministic\": true if its clients are deterministic anyway)")
        records.append(record)

    summary = {
        "project": str(project_path),
        "godot": godot,
        "run_dir": str(run_dir),
        "passed": all(r["passed"] for r in records),
        "total": len(records),
        "failed": sum(1 for r in records if not r["passed"]),
        "duration_s": round(time.monotonic() - started, 2),
        "notes": notes,
        "scenarios": records,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if not out:
        _prune(runs_root)
    return summary


def run_quick(project: str | os.PathLike, scene: str | None = None, frames: int = 120,
              capture_at: list[int] | None = None, capture_every: int = 0, overlay: bool = False,
              seed: int = 0, watch: dict | None = None, headless: bool = False, visible: bool = False,
              timeout: int = 180, refresh: bool = True, renderer: str | None = None) -> dict:
    """Boot a scene for N frames, capture it, report errors and telemetry."""
    steps: list[dict] = []
    elapsed = 0
    points = sorted(set(capture_at if capture_at is not None else ([] if headless else [frames])))
    for point in points:
        if point > elapsed:
            steps.append({"wait": point - elapsed})
            elapsed = point
        steps.append({"capture": f"t+{point}", "overlay": overlay})
    if frames > elapsed:
        steps.append({"wait": frames - elapsed})
    scenario = {"name": "run", "seed": seed, "steps": steps, "capture_every": capture_every,
                "overlay": overlay, "timeout_frames": frames + 600}
    if scene:
        scenario["scene"] = scene
    if watch:
        scenario["watch"] = watch
    return run_scenarios(project, inline=scenario, headless=headless, visible=visible,
                         timeout=timeout, refresh=refresh, renderer=renderer)


# ── Determinism ──

def _comparable(result: dict) -> dict:
    keep = ("label", "scene", "watches", "groups", "ui_text")
    return {
        "frames": result.get("frames"),
        "final_watches": result.get("final_watches"),
        "assertions": [(a.get("expr"), a.get("ok")) for a in result.get("assertions", [])],
        "observations": [{k: o.get(k) for k in keep} for o in result.get("observations", [])],
    }


def _diff(a: object, b: object, path: str, out: list[str]) -> None:
    if len(out) >= 10:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            _diff(a.get(key), b.get(key), f"{path}.{key}" if path and not path.endswith(".") else f"{path}{key}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {json.dumps(a)[:80]} vs {json.dumps(b)[:80]}")


def _process_dirs(record: dict) -> list[tuple[str, Path]]:
    """Each Godot process of a run: the run itself, or every client of a topology."""
    if record.get("clients"):
        return [(f"{c['name']}.", Path(c["out_dir"])) for c in record["clients"]]
    return [("", Path(record["out_dir"]))]


def _first_hash_difference(a: dict[int, str], b: dict[int, str]) -> str | None:
    for tick in sorted(set(a) | set(b)):
        if a.get(tick) != b.get(tick):
            return f"tick {tick}: {a.get(tick, 'missing')} vs {b.get(tick, 'missing')}"
    return None


def _merge_determinism(a: dict, b: dict) -> dict:
    merged = dict(a)
    diffs: list[str] = []
    for (label, dir_a), (_, dir_b) in zip(_process_dirs(a), _process_dirs(b)):
        try:
            ra = json.loads((dir_a / "result.json").read_text())
            rb = json.loads((dir_b / "result.json").read_text())
            _diff(_comparable(ra), _comparable(rb), label, diffs)
        except (OSError, ValueError):
            diffs.append(f"{label}one of the two runs produced no result")
        try:
            hashes = _first_hash_difference(hash_stream((dir_a / "godot.log").read_text(errors="replace")),
                                            hash_stream((dir_b / "godot.log").read_text(errors="replace")))
        except OSError:
            hashes = None
        if hashes:
            diffs.append(f"{label}HASH stream differs first at {hashes}")
    for img_a, img_b in zip(a.get("captures", []), b.get("captures", [])):
        if not sheet.same_pixels(Path(img_a), Path(img_b)):
            diffs.append(f"capture differs: {Path(img_a).name}")
    merged["deterministic"] = not diffs
    merged["nondeterminism"] = diffs
    merged["passed"] = a["passed"] and b["passed"] and not diffs
    if diffs and a["passed"] and b["passed"]:
        merged["reason"] = f"nondeterministic: {diffs[0]}"
    return merged


def _prune(runs_root: Path) -> None:
    runs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in runs[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)


# ── Text output ──

def format_summary(summary: dict) -> str:
    project = Path(summary["project"]).name
    head = (f"godot-agent: {summary['total'] - summary['failed']}/{summary['total']} passed · "
            f"{project} · {summary['duration_s']}s")
    lines = [head] + [f"  note: {n}" for n in summary.get("notes", [])]
    width = max((len(r["name"]) for r in summary["scenarios"]), default=10)
    for r in summary["scenarios"]:
        status = "PASS" if r["passed"] else "FAIL"
        detail = f"{r.get('frames', 0):>5} frames {r['wall_s']:>6}s"
        if r.get("captures"):
            detail += f"  {len(r['captures'])} captures"
        elif r.get("skipped_captures"):
            detail += f"  captures skipped ({'headless' if not r.get('rendered') else 'no image'})"
        if r.get("clients") is not None:
            detail += f"  {len(r['clients'])} clients" + (" + server" if r.get("server") else "")
        if "deterministic" in r:
            detail += ("  not compared" if r["deterministic"] is None
                       else "  deterministic" if r["deterministic"] else "  NONDETERMINISTIC")
        lines.append(f"  {status}  {r['name']:<{width}}  {detail}")
        for note in r.get("notes", []):
            lines.append(f"        note: {note}")
        if r.get("clients") is not None:
            lines += topology.format_lines(r)
        if not r["passed"]:
            lines.append(f"        reason: {r.get('reason', '')}")
            for a in r.get("failed_assertions", [])[1:4]:
                who = f"{a['client']}: " if a.get("client") else ""
                lines.append(f"        assert: {who}{a['expr']}  ({a.get('actual', '')})")
            for e in r.get("expression_errors", [])[:3]:
                lines.append(f"        expression: {e['expr']} → {e['error']}")
            for e in r.get("errors", [])[:4]:
                where = e.get("at") or (f"{e['file']}:{e['line']}" if e.get("file") else "")
                lines.append(f"        error: {e['message']}" + (f"  ({where})" if where else ""))
            for d in r.get("nondeterminism", [])[:5]:
                lines.append(f"        diff: {d}")
            for t in r.get("log_tail", [])[-8:]:
                lines.append(f"        | {t}")
            for message in r.get("shutdown", [])[:2]:
                lines.append(f"        at exit: {message}")
        elif r.get("errors"):
            lines.append(f"        {len(r['errors'])} engine errors (ignored or not failing)")
        if r.get("vars"):
            shown = ", ".join(f"{k}={json.dumps(v)[:60]}" for k, v in list(r["vars"].items())[:6])
            lines.append(f"        vars: {shown}")
        if r.get("contact_sheet"):
            lines.append(f"        sheet: {r['contact_sheet']}")
    lines.append(f"  run: {summary['run_dir']}/summary.json")
    return "\n".join(lines)
