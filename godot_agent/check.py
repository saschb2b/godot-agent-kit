"""Project health check: every script parses, optionally every scene loads, optional gdlint."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .env import RUNTIME_DIR, find_godot, refresh_class_cache, resolve_project
from .runner import parse_log

RES_PATH_RE = re.compile(r"(res://[^\s:)\"']+)(?::(\d+))?")


def _location(entry: dict) -> tuple[str, int]:
    for text in (entry.get("at", ""), entry["message"]):
        if match := RES_PATH_RE.search(text):
            return match.group(1), int(match.group(2) or 0)
    return "", 0


def check_project(project: str | os.PathLike, resources: bool = False, lint: bool = False,
                  include_addons: bool = False, refresh: bool = True, timeout: int = 600,
                  only: list[str] | None = None) -> dict:
    godot = find_godot()
    project_path = resolve_project(project)
    notes = []
    if refresh and (note := refresh_class_cache(godot, project_path)):
        notes.append(note)

    cmd = [godot, "--headless", "--path", str(project_path), "-s", str(RUNTIME_DIR / "check.gd"), "--"]
    if resources:
        cmd.append("--ga-resources")
    if include_addons:
        cmd.append("--ga-include-addons")
    if only:
        cmd.append("--ga-only=" + ",".join(only))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    text = proc.stdout.decode("utf-8", errors="replace")
    # The checker holds every loaded resource, so leak reports at exit are expected.
    run_text = text.split("GA_CHECK ", 1)[0]
    errors, warnings = parse_log(run_text)

    report = {"checked": 0, "failed": []}
    for line in text.splitlines():
        if line.startswith("GA_CHECK "):
            report = json.loads(line[len("GA_CHECK "):])

    problems = []
    for entry in errors:
        file, line = _location(entry)
        problems.append({"file": file, "line": line, "message": entry["message"]})
    # Drop duplicates: one parse error is usually reported by several loaders.
    unique = list({(p["file"], p["line"], p["message"]): p for p in problems}.values())

    result = {
        "project": str(project_path),
        "ok": proc.returncode == 0 and not unique,
        "checked": report["checked"],
        "failed_files": report["failed"],
        "errors": unique,
        "warnings": len(warnings),
        "notes": notes,
    }
    if lint:
        result["lint"] = _lint(project_path, include_addons)
    return result


def _lint(project: Path, include_addons: bool) -> list[dict] | str:
    gdlint = shutil.which("gdlint")
    if not gdlint:
        return "gdlint not installed (install.sh installs gdtoolkit)"
    files = []
    for root, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith(".") and (include_addons or Path(root, d) != project / "addons")]
        files += [str(Path(root, n)) for n in names if n.endswith(".gd")]
    if not files:
        return []
    proc = subprocess.run([gdlint, *files], capture_output=True, text=True)
    issues = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if match := re.match(r"^(.*?):(\d+): (\w+): (.*)$", line):
            issues.append({"file": os.path.relpath(match.group(1), project), "line": int(match.group(2)),
                           "level": match.group(3), "message": match.group(4)})
    return issues


def format_check(result: dict) -> str:
    status = "OK" if result["ok"] else "PROBLEMS"
    lines = [f"godot-agent check: {status} · {result['checked']} files loaded · {len(result['errors'])} errors"]
    lines += [f"  note: {n}" for n in result["notes"]]
    for p in result["errors"][:40]:
        where = f"{p['file']}:{p['line']}" if p["file"] else "(engine)"
        lines.append(f"  {where}  {p['message']}")
    if len(result["errors"]) > 40:
        lines.append(f"  ... {len(result['errors']) - 40} more")
    if result["failed_files"]:
        lines.append("  failed to load: " + ", ".join(result["failed_files"][:20]))
    lint = result.get("lint")
    if isinstance(lint, str):
        lines.append(f"  lint: {lint}")
    elif lint is not None:
        lines.append(f"  lint: {len(lint)} issues")
        lines += [f"    {i['file']}:{i['line']}  {i['message']}" for i in lint[:30]]
    return "\n".join(lines)
