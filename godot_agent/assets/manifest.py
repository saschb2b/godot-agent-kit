"""asset_manifest.json: how every generated or conformed asset was made, so it can be rebuilt."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .style import MANIFEST_FILE


def load(project: Path) -> dict:
    file = project / MANIFEST_FILE
    if file.exists():
        return json.loads(file.read_text())
    return {"version": 1, "assets": {}}


def record(project: Path, rel: str, entry: dict) -> None:
    """Adds or replaces one entry. Locked and written atomically, so several builds
    running at once (parallel agents) never lose each other's entries."""
    import fcntl
    import os
    lock_path = project / f".{MANIFEST_FILE}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = load(project)
        entry = {**entry, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        manifest["assets"][rel] = entry
        manifest["assets"] = dict(sorted(manifest["assets"].items()))
        tmp = project / f".{MANIFEST_FILE}.tmp{os.getpid()}"
        tmp.write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(tmp, project / MANIFEST_FILE)


def get(project: Path, rel: str) -> dict | None:
    return load(project)["assets"].get(rel)
