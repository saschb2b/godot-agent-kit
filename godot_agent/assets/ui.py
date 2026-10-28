"""Painted UI frames: nine-slice panels, buttons, slots and framed icons, plus Godot StyleBoxes.

Strategy-game consoles get their weight from a few painted tricks, all done here:
a bevelled trim (gilt, iron or bronze) lit from the top left with a dark outline on
both sides, studs at the corners, a fill with material (stone, wood, leather,
slate) and an inner shadow where the fill meets the trim. Fills use periodic noise
whose period is the nine-slice centre, so Godot can tile the centre seamlessly
instead of stretching it.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image

from .style import hex_to_rgb

TRIMS = {
    "gold": ("#3a2508", "#a77a2c", "#ffe9a8"),
    "iron": ("#16171b", "#5b5f68", "#d5d9e0"),
    "bronze": ("#2a1608", "#8a5a2c", "#f0c08a"),
    "wood": ("#1e1209", "#6b4526", "#c9945c"),
}
FILLS = {
    "stone": "#4f4b46",
    "wood": "#4a3020",
    "leather": "#3a2618",
    "slate": "#262a33",
}
PRESETS = ("panel", "bar", "tooltip", "button", "slot", "frame")


# ── Noise ──

def _hash(x: int, y: int, seed: int) -> float:
    h = (x * 374761393 + y * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _pnoise(x: float, y: float, cell: float, period: tuple[int, int], seed: int) -> float:
    """Value noise that repeats every `period` pixels (a multiple of `cell` is best)."""
    px, py = max(1, round(period[0] / cell)), max(1, round(period[1] / cell))
    fx, fy = x / cell, y / cell
    ix, iy = math.floor(fx), math.floor(fy)
    tx, ty = fx - ix, fy - iy
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)

    def h(i: int, j: int) -> float:
        return _hash(i % px, j % py, seed)

    a = h(ix, iy) + (h(ix + 1, iy) - h(ix, iy)) * tx
    b = h(ix, iy + 1) + (h(ix + 1, iy + 1) - h(ix, iy + 1)) * tx
    return a + (b - a) * ty


def _fbm(x: float, y: float, period: tuple[int, int], seed: int, base: float = 24.0) -> float:
    return (0.5 * _pnoise(x, y, base, period, seed) + 0.3 * _pnoise(x, y, base / 2, period, seed + 1)
            + 0.2 * _pnoise(x, y, base / 4, period, seed + 2))


def _mix(a, b, t: float):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _ramp(stops, v: float):
    """Three-colour ramp: dark at 0, base at 0.5, light at 1."""
    v = max(0.0, min(1.0, v))
    return _mix(stops[0], stops[1], v * 2) if v < 0.5 else _mix(stops[1], stops[2], (v - 0.5) * 2)


# ── Fills ──

def _fill_color(kind: str, base, x: int, y: int, period: tuple[int, int], seed: int):
    """Colour of a fill at (x, y), periodic in `period` (the nine-slice centre)."""
    n = _fbm(x, y, period, seed)
    if kind == "wood":
        plank = 16
        row = y // plank
        f = (y % plank) / plank
        grain = _pnoise(x, y * 6, 6, (period[0], period[1] * 6), seed + row)
        value = 0.82 + 0.22 * grain + 0.12 * (_hash(row % max(1, period[1] // plank), 0, seed) - 0.5)
        value *= 1.0 - 0.45 * (f < 0.08) + 0.08 * (0.3 < f < 0.6)
        return tuple(c * value for c in base)
    if kind == "stone":
        bw, bh = 32, 16
        row = y // bh
        xs = x + (bw // 2 if row % 2 else 0)
        fx, fy = (xs % bw) / bw, (y % bh) / bh
        block = _hash((xs // bw) % max(1, period[0] // bw), row % max(1, period[1] // bh), seed + 7)
        mortar = min(fx, 1 - fx) * bw < 1.2 or min(fy, 1 - fy) * bh < 1.2
        value = (0.82 + 0.2 * block) * (0.85 + 0.3 * n)
        if mortar:
            value *= 0.6
        elif fy > 0.8:
            value *= 1.08
        return tuple(c * value for c in base)
    if kind == "leather":
        fine = _pnoise(x, y, 3, period, seed + 3)
        return tuple(c * (0.8 + 0.25 * n + 0.1 * fine) for c in base)
    # slate: smooth, dark, a little cloudy
    return tuple(c * (0.85 + 0.3 * n) for c in base)


# ── Frames ──

def _paint_frame(img: Image.Image, border: int, trim: str, fill: str | None, fill_color, margin: int,
                 seed: int, inverted: bool = False, glow: tuple | None = None, studs: bool = True,
                 shadow: int = 6) -> None:
    """Paints fill, inner shadow, bevelled trim and corner studs onto an RGBA image in place."""
    w, h = img.size
    stops = [hex_to_rgb(c) for c in TRIMS[trim]]
    base = hex_to_rgb(fill_color) if fill_color else (hex_to_rgb(FILLS[fill]) if fill else None)
    period = (max(1, w - 2 * margin), max(1, h - 2 * margin))
    light = (-0.7071, -0.7071) if not inverted else (0.7071, 0.7071)
    px = img.load()
    for y in range(h):
        for x in range(w):
            d_left, d_top, d_right, d_bottom = x, y, w - 1 - x, h - 1 - y
            d = min(d_left, d_top, d_right, d_bottom)
            if d < border:
                # Bead across the trim: rises from the outer edge, falls to the inner.
                t = (d + 0.5) / border
                normal = ((-1, 0) if d == d_left else (0, -1) if d == d_top else (1, 0) if d == d_right else (0, 1))
                slope = math.cos(math.pi * t)
                tilt = (normal[0] * slope, normal[1] * slope)
                lit = tilt[0] * light[0] + tilt[1] * light[1]
                grain = _pnoise(x, y, 3, (w, h), seed + 11)
                value = 0.5 + 0.48 * lit + 0.14 * (grain - 0.5)
                color = _ramp(stops, value)
                if lit > 0.55:
                    color = _mix(color, stops[2], (lit - 0.55) * 1.2)
                if d == 0 or d == border - 1:
                    color = _mix(color, (8, 6, 4), 0.75)
                px[x, y] = (*(int(max(0, min(255, c))) for c in color), 255)
            elif base is not None:
                color = _fill_color(fill or "slate", base, x - margin, y - margin, period, seed)
                inner = d - border
                if inner < shadow:
                    color = tuple(c * (0.55 + 0.45 * inner / shadow) for c in color)
                if glow and inner < 4:
                    color = _mix(color, glow, 0.55 * (1 - inner / 4))
                px[x, y] = (*(int(max(0, min(255, c))) for c in color), 255)
    if border >= 6 and base is not None:
        _inner_line(img, border + 2, 2, stops, light)
    if studs:
        plate = border * 2 + 4
        r = max(2, border // 2 + 1)
        for cx, cy in ((plate // 2, plate // 2), (w - 1 - plate // 2, plate // 2),
                       (plate // 2, h - 1 - plate // 2), (w - 1 - plate // 2, h - 1 - plate // 2)):
            if border >= 6:
                _corner_plate(img, cx, cy, plate // 2, stops, light, seed)
            _stud(img, cx, cy, r + 1, stops, light)


def _inner_line(img: Image.Image, at: int, width: int, stops, light) -> None:
    """A thin second bead inside the trim, the double line of a carved frame."""
    w, h = img.size
    px = img.load()
    for y in range(h):
        for x in range(w):
            d_left, d_top, d_right, d_bottom = x, y, w - 1 - x, h - 1 - y
            d = min(d_left, d_top, d_right, d_bottom)
            if not at <= d < at + width:
                continue
            normal = ((-1, 0) if d == d_left else (0, -1) if d == d_top else (1, 0) if d == d_right else (0, 1))
            side = 1 if d == at else -1
            lit = side * (normal[0] * light[0] + normal[1] * light[1])
            px[x, y] = (*(int(c) for c in _ramp(stops, 0.5 + 0.35 * lit)), 255)


def _corner_plate(img: Image.Image, cx: int, cy: int, half: int, stops, light, seed: int) -> None:
    """A bevelled square plate over each corner, like cast metal fittings."""
    px = img.load()
    bevel = max(2, half // 3)
    for y in range(cy - half, cy + half + 1):
        for x in range(cx - half, cx + half + 1):
            if not (0 <= x < img.width and 0 <= y < img.height):
                continue
            dl, dt, dr, db = x - (cx - half), y - (cy - half), (cx + half) - x, (cy + half) - y
            d = min(dl, dt, dr, db)
            if d == 0:
                color = _mix(stops[0], (0, 0, 0), 0.6)
            elif d < bevel:
                normal = ((-1, 0) if d == dl else (0, -1) if d == dt else (1, 0) if d == dr else (0, 1))
                lit = normal[0] * light[0] + normal[1] * light[1]
                color = _ramp(stops, 0.5 + 0.45 * lit)
            else:
                grain = _pnoise(x, y, 2, (img.width, img.height), seed + 5)
                color = _ramp(stops, 0.45 + 0.15 * grain)
            px[x, y] = (*(int(c) for c in color), 255)


def _stud(img: Image.Image, cx: int, cy: int, r: int, stops, light) -> None:
    """A domed rivet: dark rim, lit upper left, specular dot."""
    px = img.load()
    w, h = img.size
    for y in range(cy - r - 1, cy + r + 2):
        for x in range(cx - r - 1, cx + r + 2):
            if not (0 <= x < w and 0 <= y < h):
                continue
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist > r + 0.5:
                continue
            if dist > r - 0.6:
                color = _mix(stops[0], (0, 0, 0), 0.5)
            else:
                nx, ny = dx / max(r, 1), dy / max(r, 1)
                lit = -(nx * light[0] + ny * light[1])
                color = _ramp(stops, 0.55 - 0.45 * lit)
                if math.hypot(dx + r * 0.35, dy + r * 0.35) < r * 0.3:
                    color = _mix(color, stops[2], 0.8)
            px[x, y] = (*(int(c) for c in color), 255)


def _diamond(img: Image.Image, cx: int, cy: int, r: int, trim: str) -> None:
    """A small gem-cut ornament for the middle of long edges."""
    stops = [hex_to_rgb(c) for c in TRIMS[trim]]
    px = img.load()
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            d = abs(x - cx) + abs(y - cy)
            if d > r or not (0 <= x < img.width and 0 <= y < img.height):
                continue
            lit = 1.0 if (x - cx) + (y - cy) < 0 else 0.15
            color = _ramp(stops, 0.3 + 0.6 * lit) if d < r else stops[0]
            px[x, y] = (*(int(c) for c in color), 255)


def paint(preset: str, size: tuple[int, int], *, fill: str = "stone", trim: str = "gold",
          fill_color: str | None = None, border: int | None = None, seed: int = 1,
          state: str = "normal") -> tuple[Image.Image, dict]:
    """One UI image and its nine-slice margins: {"margin": px, "content": px}."""
    if preset not in PRESETS:
        raise ValueError(f"unknown UI preset {preset!r}; choose from {', '.join(PRESETS)}")
    if trim not in TRIMS:
        raise ValueError(f"unknown trim {trim!r}; choose from {', '.join(TRIMS)}")
    if fill not in FILLS:
        raise ValueError(f"unknown fill {fill!r}; choose from {', '.join(FILLS)}")
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if preset in ("panel", "tooltip"):
        b = border or (10 if preset == "panel" else 5)
        margin = b * 2 + 6
        _paint_frame(img, b, trim, fill, fill_color, margin, seed, studs=preset == "panel")
        if preset == "panel" and w >= 96:
            _diamond(img, w // 2, b // 2, b // 2 + 2, trim)
            _diamond(img, w // 2, h - 1 - b // 2, b // 2 + 2, trim)
        return img, {"margin": margin, "content": b + 6}
    if preset == "bar":
        b = border or 5
        margin = b + 6
        _paint_frame(img, b, trim, fill, fill_color, margin, seed, studs=True, shadow=4)
        return img, {"margin": margin, "content": b + 4}
    if preset == "slot":
        b = border or 3
        _paint_frame(img, b, "iron", fill, fill_color or "#15171c", b + 4, seed, inverted=True, studs=False,
                     shadow=10)
        return img, {"margin": b + 4, "content": b + 2}
    if preset == "frame":
        # Border only, transparent centre: an overlay for portraits and minimaps. The
        # nine-slice margin covers the corner plates, or Godot and CSS would repeat
        # pieces of them along the edges.
        b = border or 6
        margin = b * 2 + 5
        _paint_frame(img, b, trim, None, None, margin, seed, studs=True)
        return img, {"margin": margin, "content": b + 2}
    # button: state changes lighting, glow and colour
    b = border or 4
    glow = hex_to_rgb(TRIMS[trim][2]) if state == "hover" else None
    _paint_frame(img, b, trim, fill, fill_color, b + 6, seed, inverted=state == "pressed", glow=glow, studs=False,
                 shadow=5)
    if state == "disabled":
        img = _desaturate(img, 0.75, 0.55)
    return img, {"margin": b + 6, "content": b + 3}


def _desaturate(img: Image.Image, amount: float, value: float) -> Image.Image:
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            grey = 0.3 * r + 0.59 * g + 0.11 * b
            px[x, y] = (int((r + (grey - r) * amount) * value), int((g + (grey - g) * amount) * value),
                        int((b + (grey - b) * amount) * value), a)
    return out


# ── Icons ──

def icon_frame(icon: Image.Image, trim: str = "gold", background: str = "#2b3d5c", border: int = 3,
               seed: int = 1) -> Image.Image:
    """A command-card icon: painted radial backdrop, the subject, a bevelled border."""
    w, h = icon.size
    rng = random.Random(seed)
    center = hex_to_rgb(background)
    edge = tuple(c * 0.35 for c in center)
    out = Image.new("RGBA", (w, h))
    px = out.load()
    for y in range(h):
        for x in range(w):
            d = math.hypot((x - w * 0.45) / w, (y - h * 0.4) / h) / 0.7
            n = _pnoise(x, y, 6, (w, h), seed)
            color = _mix(center, edge, min(1.0, d)) if d < 1 else edge
            color = tuple(c * (0.9 + 0.2 * n) for c in color)
            px[x, y] = (*(int(c) for c in color), 255)
    out.alpha_composite(icon)
    stops = [hex_to_rgb(c) for c in TRIMS[trim]]
    for y in range(h):
        for x in range(w):
            d = min(x, y, w - 1 - x, h - 1 - y)
            if d >= border:
                continue
            top_left = x + y < (w + h) / 2 and (x == d or y == d)
            value = 0.85 if top_left else 0.25
            color = _ramp(stops, value + rng.uniform(-0.05, 0.05))
            if d == 0:
                color = _mix(color, (0, 0, 0), 0.6)
            px[x, y] = (*(int(c) for c in color), 255)
    return out


# ── Godot resources ──

def stylebox_tres(texture_res: str, margin: int, content: int, tile: bool = True) -> str:
    """A StyleBoxTexture that nine-slices the image and tiles (not stretches) its middle."""
    stretch = 2 if tile else 0  # AXIS_STRETCH_MODE_TILE_FIT
    lines = ['[gd_resource type="StyleBoxTexture" load_steps=2 format=3]', "",
             f'[ext_resource type="Texture2D" path="{texture_res}" id="1"]', "", "[resource]",
             'texture = ExtResource("1")']
    for side in ("left", "top", "right", "bottom"):
        lines.append(f"texture_margin_{side} = {float(margin)}")
    for side in ("left", "top", "right", "bottom"):
        lines.append(f"content_margin_{side} = {float(content)}")
    lines += [f"axis_stretch_horizontal = {stretch}", f"axis_stretch_vertical = {stretch}", ""]
    return "\n".join(lines)


def write(preset: str, dest: Path, res_base: str, size: tuple[int, int], **options) -> list[Path]:
    """Writes the PNG(s) and StyleBoxTexture(s) for a preset; buttons get one per state."""
    written = []
    states = ("normal", "hover", "pressed", "disabled") if preset == "button" else (None,)
    for state in states:
        suffix = f"_{state}" if state else ""
        img, margins = paint(preset, size, state=state or "normal", **options)
        png = dest.with_name(dest.name + suffix + ".png")
        png.parent.mkdir(parents=True, exist_ok=True)
        img.save(png)
        tres = png.with_suffix(".tres")
        tres.write_text(stylebox_tres(f"{res_base}{suffix}.png", margins["margin"], margins["content"]))
        written += [png, tres]
    return written
