"""The style spec: which rules apply to which asset paths, and the palettes they use.

A project keeps its spec in `asset_style.json` at the project root:

    {
      "version": 1,
      "background": "#28141e",
      "palettes": {"icons": "palettes/icons.gpl"},
      "ignore": ["assets/third_party/**"],
      "rules": [
        {"match": "**/*.png", "image": {"binary_alpha": true}},
        {"match": "assets/icons/*.png", "image": {"size": [16, 16], "palette": "icons", "max_colors": 16}},
        {"match": "assets/audio/sfx/**", "audio": {"max_peak_db": -1.0, "max_leading_silence_ms": 30}},
        {"match": "models/props/*.glb", "model": {"max_tris": 300, "origin": "bottom-center",
                                                  "colors": {"leaf": "#c8662a"}}}
      ]
    }

Every rule whose glob matches a path applies, in order; later rules override
earlier keys, the way CSS cascades. Paths are relative to the project root.
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from ..env import GodotAgentError, project_setting

STYLE_FILE = "asset_style.json"
MANIFEST_FILE = "asset_manifest.json"
IMAGE_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg", ".bmp", ".tga"}
AUDIO_EXTENSIONS = {".wav", ".ogg", ".mp3", ".flac"}
MODEL_EXTENSIONS = {".glb", ".gltf"}


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern:
    """Glob with ** (any depth), * (within one folder), ? and {a,b} alternatives."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "{":
            end = pattern.index("}", i)
            out += "(?:" + "|".join(re.escape(p) for p in pattern[i + 1:end].split(",")) + ")"
            i = end
        else:
            out += re.escape(c)
        i += 1
    return re.compile(f"^{out}$")


def matches(pattern: str, rel_path: str) -> bool:
    return bool(glob_to_regex(pattern).match(rel_path))


def rel_path(project: Path, path: str | Path) -> str:
    """res://a/b.png, a/b.png or /abs/project/a/b.png -> a/b.png"""
    text = str(path)
    if text.startswith("res://"):
        return text[len("res://"):]
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(project).as_posix()
        except ValueError as exc:
            raise GodotAgentError(f"{candidate} is outside the project {project}") from exc
    return candidate.as_posix()


def kind_of(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in MODEL_EXTENSIONS:
        return "model"
    return None


class Style:
    def __init__(self, project: Path, data: dict, source: Path | None):
        self.project = project
        self.data = data
        self.source = source
        self.background = data.get("background") or default_background(project)
        self._palettes: dict[str, list[tuple[int, int, int]]] = {}

    @classmethod
    def load(cls, project: Path, path: str | Path | None = None) -> "Style":
        file = Path(path).expanduser() if path else project / STYLE_FILE
        if not file.exists():
            if path:
                raise GodotAgentError(f"No style file at {file}")
            return cls(project, {"version": 1, "rules": []}, None)
        try:
            data = json.loads(file.read_text())
        except ValueError as exc:
            raise GodotAgentError(f"{file}: invalid JSON: {exc}") from exc
        return cls(project, data, file)

    def ignored(self, rel: str) -> bool:
        return any(matches(p, rel) for p in self.data.get("ignore", []))

    def rule_for(self, rel: str, kind: str) -> dict:
        """Merged rule for one path: every matching rule in order, later keys win."""
        merged: dict = {}
        for rule in self.data.get("rules", []):
            patterns = rule["match"] if isinstance(rule["match"], list) else [rule["match"]]
            if any(matches(p, rel) for p in patterns) and kind in rule:
                for key, value in rule[kind].items():
                    if value is None:
                        # A more specific rule can lift a constraint: {"size": null}.
                        merged.pop(key, None)
                    elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                        merged[key] = {**merged[key], **value}
                    else:
                        merged[key] = value
        return merged

    def palette(self, ref: str | list | None) -> list[tuple[int, int, int]] | None:
        """A palette by name (from "palettes"), file path, or inline list of hex colors."""
        if ref is None:
            return None
        if isinstance(ref, list):
            return [hex_to_rgb(c) for c in ref]
        if ref in self._palettes:
            return self._palettes[ref]
        target = self.data.get("palettes", {}).get(ref, ref)
        if isinstance(target, list):
            colors = [hex_to_rgb(c) for c in target]
        else:
            file = Path(target).expanduser()
            if not file.is_absolute():
                file = self.project / rel_path(self.project, target)
            if not file.exists():
                raise GodotAgentError(f"Palette '{ref}' not found at {file}")
            colors = read_palette(file)
        self._palettes[ref] = colors
        return colors


# ── Colors and palette files ──

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: tuple[int, ...]) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def luminance(rgb: tuple[int, ...]) -> float:
    """Relative luminance (WCAG), 0 for black to 1 for white."""
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def read_palette(path: Path) -> list[tuple[int, int, int]]:
    """GIMP .gpl (what Aseprite and most tools exchange), .hex (one color per line) or .json list."""
    text = path.read_text()
    if path.suffix == ".json":
        return [hex_to_rgb(c) for c in json.loads(text)]
    colors = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("GIMP", "Name:", "Columns:", "#")):
            continue
        if path.suffix == ".gpl":
            parts = line.split()
            if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
                colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
        else:
            colors.append(hex_to_rgb(line))
    return colors


def write_palette(path: Path, name: str, colors: list[tuple[int, int, int]]) -> None:
    lines = ["GIMP Palette", f"Name: {name}", "Columns: 8", "#"]
    lines += [f"{r:3d} {g:3d} {b:3d}\t{rgb_to_hex((r, g, b))}" for r, g, b in colors]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def default_background(project: Path) -> str:
    """The project's clear color, i.e. what sprites are actually seen against."""
    value = project_setting(project, "rendering", "environment/defaults/default_clear_color") or ""
    match = re.match(r"Color\(([\d.]+),\s*([\d.]+),\s*([\d.]+)", value)
    if not match:
        return "#4d4d4d"  # Godot's default clear color
    return rgb_to_hex(tuple(round(float(v) * 255) for v in match.groups()))


def _git_files(project: Path) -> set[str] | None:
    """Tracked and untracked-but-not-ignored files, or None outside a git repository."""
    try:
        proc = subprocess.run(["git", "-C", str(project), "ls-files", "-co", "--exclude-standard", "--full-name"],
                              capture_output=True, text=True, timeout=60)
        top = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not top:
        return None
    prefix = project.relative_to(Path(top).resolve()).as_posix() if project != Path(top).resolve() else ""
    files = set()
    for line in proc.stdout.splitlines():
        if not prefix:
            files.add(line)
        elif line.startswith(prefix + "/"):
            files.add(line[len(prefix) + 1:])
    return files


def iter_assets(project: Path, paths: list[str] | None = None, include_addons: bool = False) -> list[str]:
    """Asset files (relative paths) under the given paths, or the whole project.

    Skips hidden folders, gitignored files (build output, test reports) and,
    unless asked, addons/: third-party plugins are not the game's art.
    """
    roots = [project / rel_path(project, p) for p in paths] if paths else [project]
    allowed = _git_files(project)
    found = []
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        for file in candidates:
            rel = file.relative_to(project).as_posix()
            parts = Path(rel).parts
            if not file.is_file() or not kind_of(rel) or any(part.startswith(".") for part in parts):
                continue
            if parts[0] == "addons" and not include_addons and not paths:
                continue
            if allowed is not None and rel not in allowed:
                continue
            found.append(rel)
    return sorted(set(found))
