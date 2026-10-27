"""Aseprite file -> packed sprite sheet + Godot SpriteFrames resource, one animation per tag."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..env import GodotAgentError


def export_sheet(source: Path, sheet: Path) -> dict:
    aseprite = shutil.which("aseprite")
    if not aseprite:
        raise GodotAgentError("aseprite not found on PATH")
    sheet.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "sheet.json"
        proc = subprocess.run([aseprite, "-b", str(source), "--sheet", str(sheet), "--sheet-type", "packed",
                               "--data", str(data_file), "--format", "json-array", "--list-tags"],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not data_file.exists():
            raise GodotAgentError(f"aseprite export failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return json.loads(data_file.read_text())


def _tag_frames(tag: dict) -> list[int]:
    forward = list(range(tag["from"], tag["to"] + 1))
    direction = tag.get("direction", "forward")
    if direction == "reverse":
        return forward[::-1]
    if direction == "pingpong":
        return forward + forward[-2:0:-1]
    if direction == "pingpong_reverse":
        backward = forward[::-1]
        return backward + backward[-2:0:-1]
    return forward


def animations_from_data(data: dict) -> list[dict]:
    frames = data["frames"]
    tags = data.get("meta", {}).get("frameTags") or [{"name": "default", "from": 0, "to": len(frames) - 1}]
    animations = []
    for tag in tags:
        indices = _tag_frames(tag)
        durations = [frames[i]["duration"] for i in indices]
        # Godot plays an animation at `speed` frames per second and lets each frame
        # last a multiple of that, so the shortest frame becomes duration 1.0.
        base_ms = min(durations) or 100
        repeat = str(tag.get("repeat", "0"))
        animations.append({
            "name": tag["name"],
            "speed": round(1000 / base_ms, 3),
            "loop": repeat in ("", "0"),
            "frames": [(i, round(d / base_ms, 3)) for i, d in zip(indices, durations)],
        })
    return animations


def write_sprite_frames(path: Path, sheet_res_path: str, data: dict) -> list[dict]:
    """Writes a Godot 4 SpriteFrames .tres whose frames are AtlasTexture regions of the sheet."""
    frames = data["frames"]
    animations = animations_from_data(data)
    used = sorted({i for anim in animations for i, _ in anim["frames"]})
    lines = ['[gd_resource type="SpriteFrames" format=3]', "",
             f'[ext_resource type="Texture2D" path="{sheet_res_path}" id="1_sheet"]', ""]
    for i in used:
        rect = frames[i]["frame"]
        lines += [f'[sub_resource type="AtlasTexture" id="AtlasTexture_{i}"]', 'atlas = ExtResource("1_sheet")',
                  f"region = Rect2({rect['x']}, {rect['y']}, {rect['w']}, {rect['h']})", ""]
    blocks = []
    for anim in animations:
        frame_blocks = ", ".join(
            f'{{\n"duration": {duration},\n"texture": SubResource("AtlasTexture_{i}")\n}}' for i, duration in anim["frames"])
        blocks.append(f'{{\n"frames": [{frame_blocks}],\n"loop": {"true" if anim["loop"] else "false"},\n'
                      f'"name": &"{anim["name"]}",\n"speed": {anim["speed"]}\n}}')
    lines += ["[resource]", f"animations = [{', '.join(blocks)}]", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return animations
