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
    manifest = load(project)
    entry = {**entry, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    manifest["assets"][rel] = entry
    manifest["assets"] = dict(sorted(manifest["assets"].items()))
    (project / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n")


def get(project: Path, rel: str) -> dict | None:
    return load(project)["assets"].get(rel)
