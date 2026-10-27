"""Locating Godot, cache paths, the virtual display and the class-cache refresh."""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = KIT_ROOT / "runtime"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "godot-agent" / "config.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "godot-agent"


class GodotAgentError(Exception):
    """An error with a message meant for the user or agent, not a traceback."""


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def find_godot() -> str:
    """Godot binary from GODOT_PATH, the config file, PATH or common install folders."""
    candidates = [os.environ.get("GODOT_PATH"), os.environ.get("GODOT_BIN"), load_config().get("godot")]
    candidates += [shutil.which(name) for name in ("godot", "godot4", "Godot")]
    home = Path.home()
    patterns = [home / "Applications" / "Godot_v4*", home / ".local" / "bin" / "Godot_v4*",
                Path("/opt") / "godot*" / "Godot_v4*", Path("/usr/local/bin") / "Godot_v4*"]
    for pattern in patterns:
        # Newest version first, skipping mono/.NET builds and archives.
        matches = [m for m in glob.glob(str(pattern)) if not m.endswith((".zip", ".tar.xz")) and "mono" not in m]
        candidates += sorted(matches, reverse=True)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise GodotAgentError("Godot not found. Set GODOT_PATH or run: godot-agent doctor --set-godot /path/to/godot")


def godot_version(godot: str) -> str:
    out = subprocess.run([godot, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    return out.splitlines()[-1] if out else "unknown"


def version_tag(version: str) -> str:
    """4.7.1.stable.official.a13da4feb -> 4.7.1-stable (the upstream git tag)."""
    match = re.match(r"(\d+\.\d+(?:\.\d+)?)\.([a-z]+\d*)", version)
    return f"{match.group(1)}-{match.group(2)}" if match else version


def resolve_project(path: str | os.PathLike) -> Path:
    project = Path(path).expanduser().resolve()
    if project.is_file() and project.name == "project.godot":
        project = project.parent
    if not (project / "project.godot").is_file():
        raise GodotAgentError(f"No project.godot in {project}")
    return project


def project_setting(project: Path, section: str, key: str) -> str | None:
    """Reads one value from project.godot without a Godot process."""
    current = None
    for line in (project / "project.godot").read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def project_name(project: Path) -> str:
    name = project_setting(project, "application", "config/name") or project.name
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "project"


def refresh_class_cache(godot: str, project: Path, force: bool = False, timeout: int = 900) -> str | None:
    """Re-imports the project when scripts are newer than Godot's class cache.

    Scripts added outside the editor (by an agent, git pull, codegen) are missing
    from .godot/global_script_class_cache.cfg, so their class_name types fail to
    resolve when the game runs. Returns a short note when it refreshed, else None.
    """
    cache = project / ".godot" / "global_script_class_cache.cfg"
    stale = force or not cache.exists()
    if not stale:
        cache_time = cache.stat().st_mtime
        for root, dirs, files in os.walk(project):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if any(f.endswith(".gd") and os.path.getmtime(os.path.join(root, f)) > cache_time for f in files):
                stale = True
                break
    if not stale:
        return None
    started = time.monotonic()
    print(f"godot-agent: refreshing class cache for {project.name} (godot --import)...", file=sys.stderr)
    proc = subprocess.run([godot, "--headless", "--path", str(project), "--import"],
                          capture_output=True, text=True, timeout=timeout)
    if cache.exists():
        cache.touch()
    note = f"refreshed class cache ({time.monotonic() - started:.1f}s)"
    if proc.returncode != 0:
        note += f"; import exited {proc.returncode}"
    return note


@contextlib.contextmanager
def virtual_display(width: int = 1920, height: int = 1080):
    """Starts a private Xvfb server and yields its DISPLAY, or None when Xvfb is missing."""
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        yield None
        return
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [xvfb, "-displayfd", str(write_fd), "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp", "-noreset"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        # Xvfb writes the display number it picked once it is ready for clients.
        with os.fdopen(read_fd) as pipe:
            number = pipe.readline().strip()
        if not number:
            raise GodotAgentError("Xvfb failed to start")
        yield f":{number}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
