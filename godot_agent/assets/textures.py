"""Painted, seamless terrain textures: grass, meadow, path, dirt, sand, mud, rock, cobble.

Strategy-game ground reads as hand-painted because each texture is built the way a
painter would lay it down: a mottled base in a few tones of one colour, then many
small marks on top (blade strokes lit at the tip, pebbles lit from the top left with
a shadow below, cracks, flowers). Everything here wraps at the edges, so the
texture tiles without seams; the terrain shader breaks up the repetition.
"""

from __future__ import annotations

import math
import random

from PIL import Image

from .style import hex_to_rgb
from .ui import _fbm, _pnoise

PRESETS: dict[str, dict] = {
    "grass": {"colors": {"dark": "#2f5a22", "mid": "#4f8a32", "light": "#79ad45", "tip": "#a8cf63"},
              "strokes": 1.0, "flowers": 0.0},
    "meadow": {"colors": {"dark": "#3d6526", "mid": "#5f9238", "light": "#88b84e", "tip": "#b5d56c",
                          "flower_a": "#f2d15a", "flower_b": "#f4efe0", "flower_c": "#b590d8"},
               "strokes": 0.9, "flowers": 1.0},
    "path": {"colors": {"dark": "#5a3f27", "mid": "#86633e", "light": "#a9875a", "pebble": "#b8a88c"},
             "pebbles": 1.0, "cracks": 0.4},
    "dirt": {"colors": {"dark": "#4a3322", "mid": "#6e5034", "light": "#8f6f4b", "pebble": "#a39274"},
             "pebbles": 0.5, "cracks": 1.0},
    "sand": {"colors": {"dark": "#9c8558", "mid": "#c2aa78", "light": "#ddc999", "pebble": "#efe4c8"},
             "pebbles": 0.25, "cracks": 0.0, "grain": 1.0},
    "mud": {"colors": {"dark": "#2a261b", "mid": "#3f3826", "light": "#57503a", "pebble": "#6c6550"},
            "pebbles": 0.3, "cracks": 0.0, "sheen": 1.0},
    "rock": {"colors": {"dark": "#4b4843", "mid": "#76716a", "light": "#a19b91", "lichen": "#8a9a4a"},
             "cells": 22.0, "grout": 0.0},
    "cobble": {"colors": {"dark": "#47433d", "mid": "#7d776e", "light": "#aaa398", "lichen": "#6f7f3c"},
               "cells": 14.0, "grout": 1.0},
}


def _ramp(stops: list[tuple], v: float) -> tuple:
    v = max(0.0, min(1.0, v)) * (len(stops) - 1)
    i = min(int(v), len(stops) - 2)
    f = v - i
    a, b = stops[i], stops[i + 1]
    return tuple(a[k] + (b[k] - a[k]) * f for k in range(3))


def _mix(a, b, t: float) -> tuple:
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


class _Canvas:
    """RGB float canvas that wraps at the edges, so every mark tiles."""

    def __init__(self, size: int):
        self.size = size
        self.px = [[(0.0, 0.0, 0.0)] * size for _ in range(size)]

    def get(self, x: int, y: int) -> tuple:
        return self.px[y % self.size][x % self.size]

    def put(self, x: int, y: int, color, alpha: float = 1.0) -> None:
        x, y = x % self.size, y % self.size
        self.px[y][x] = _mix(self.px[y][x], color, alpha) if alpha < 1.0 else tuple(color)

    def image(self) -> Image.Image:
        img = Image.new("RGB", (self.size, self.size))
        img.putdata([tuple(int(max(0, min(255, c))) for c in p) for row in self.px for p in row])
        return img


def _base(canvas: _Canvas, stops: list, seed: int, cell: float, contrast: float = 1.0) -> None:
    size = canvas.size
    period = (size, size)
    for y in range(size):
        for x in range(size):
            v = 0.55 * _fbm(x, y, period, seed, cell) + 0.45 * _pnoise(x, y, cell / 6, period, seed + 9)
            canvas.px[y][x] = _ramp(stops, 0.5 + (v - 0.5) * 1.6 * contrast)


def _strokes(canvas: _Canvas, rng: random.Random, colors: dict, density: float) -> None:
    """Grass blades: short upward strokes, shadowed at the root and lit at the tip."""
    dark, mid, light, tip = (hex_to_rgb(colors[k]) for k in ("dark", "mid", "light", "tip"))
    count = int(canvas.size * canvas.size / 22 * density)
    for _ in range(count):
        x, y = rng.uniform(0, canvas.size), rng.uniform(0, canvas.size)
        angle = math.radians(-90 + rng.uniform(-38, 38))
        length = rng.uniform(4, 10)
        shade = rng.random()
        body = _mix(mid, light, shade)
        end = _mix(body, tip, 0.6 + 0.4 * shade)
        steps = int(length * 1.5)
        for i in range(steps):
            f = i / max(1, steps - 1)
            px, py = x + math.cos(angle) * length * f, y + math.sin(angle) * length * f
            # A dark edge on one side gives each blade a little volume.
            canvas.put(int(px) + 1, int(py), _mix(dark, body, 0.35), 0.45 * (1 - f))
            canvas.put(int(px), int(py), _mix(body, end, f), 0.85)


def _flowers(canvas: _Canvas, rng: random.Random, colors: dict, density: float) -> None:
    petals = [hex_to_rgb(colors[k]) for k in ("flower_a", "flower_b", "flower_c") if k in colors]
    for _ in range(int(canvas.size * canvas.size / 900 * density)):
        x, y = rng.randrange(canvas.size), rng.randrange(canvas.size)
        color = rng.choice(petals)
        for dx, dy in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)):
            canvas.put(x + dx, y + dy, color if (dx, dy) != (0, 0) else _mix(color, (255, 220, 90), 0.6), 0.9)
        canvas.put(x + 1, y + 2, (20, 30, 10), 0.35)


def _pebbles(canvas: _Canvas, rng: random.Random, colors: dict, density: float) -> None:
    """Small stones lit from the top left, each with a soft shadow below it."""
    dark, pebble = hex_to_rgb(colors["dark"]), hex_to_rgb(colors["pebble"])
    for _ in range(int(canvas.size * canvas.size / 160 * density)):
        cx, cy = rng.uniform(0, canvas.size), rng.uniform(0, canvas.size)
        rx, ry = rng.uniform(1.2, 3.6), rng.uniform(1.0, 2.8)
        tone = _mix(pebble, dark, rng.uniform(0.0, 0.45))
        for y in range(int(cy - ry - 2), int(cy + ry + 3)):
            for x in range(int(cx - rx - 2), int(cx + rx + 3)):
                d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
                ds = ((x - cx - 0.8) / rx) ** 2 + ((y - cy - 1.4) / ry) ** 2
                if d <= 1.0:
                    light = 1.0 + 0.45 * (-(x - cx) / rx - (y - cy) / ry) * 0.5
                    canvas.put(x, y, tuple(c * light for c in tone))
                elif ds <= 1.0:
                    canvas.put(x, y, dark, 0.55)


def _cracks(canvas: _Canvas, rng: random.Random, colors: dict, density: float) -> None:
    dark = hex_to_rgb(colors["dark"])
    for _ in range(int(canvas.size * canvas.size / 9000 * density) + (1 if density else 0)):
        x, y = rng.uniform(0, canvas.size), rng.uniform(0, canvas.size)
        angle = rng.uniform(0, math.tau)
        for _ in range(rng.randint(10, 34)):
            angle += rng.uniform(-0.6, 0.6)
            x, y = x + math.cos(angle), y + math.sin(angle)
            canvas.put(int(x), int(y), _mix(dark, (0, 0, 0), 0.3), 0.75)
            canvas.put(int(x) + 1, int(y) + 1, hex_to_rgb(colors["light"]), 0.2)


def _grain(canvas: _Canvas, rng: random.Random, amount: float) -> None:
    for y in range(canvas.size):
        for x in range(canvas.size):
            k = 1.0 + rng.uniform(-0.09, 0.09) * amount
            canvas.px[y][x] = tuple(c * k for c in canvas.px[y][x])


def _sheen(canvas: _Canvas, colors: dict, seed: int) -> None:
    """Wet mud: lighter, smoother patches where water stands."""
    light = hex_to_rgb(colors["light"])
    size = canvas.size
    for y in range(size):
        for x in range(size):
            v = _pnoise(x, y, 24, (size, size), seed + 31)
            if v > 0.62:
                canvas.px[y][x] = _mix(canvas.px[y][x], light, min(1.0, (v - 0.62) * 3.0) * 0.6)


def _stones(canvas: _Canvas, rng: random.Random, colors: dict, cell: float, grout: float, seed: int) -> None:
    """Rock plates or cobbles: jittered cells, each tinted, lit at its upper-left rim."""
    dark, mid, light = (hex_to_rgb(colors[k]) for k in ("dark", "mid", "light"))
    lichen = hex_to_rgb(colors["lichen"])
    size = canvas.size
    n = max(2, round(size / cell))
    step = size / n
    points = {}
    for j in range(n):
        for i in range(n):
            points[(i, j)] = ((i + 0.15 + 0.7 * rng.random()) * step, (j + 0.15 + 0.7 * rng.random()) * step,
                              rng.random())
    for y in range(size):
        for x in range(size):
            ci, cj = int(x / step), int(y / step)
            best, second, best_p = 1e9, 1e9, None
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    key = ((ci + di) % n, (cj + dj) % n)
                    px, py, tone = points[key]
                    px += (ci + di - key[0]) * step
                    py += (cj + dj - key[1]) * step
                    d = math.hypot(x - px, y - py)
                    if d < best:
                        best, second, best_p = d, best, (px, py, tone)
                    elif d < second:
                        second = d
            edge = second - best
            px, py, tone = best_p
            base = _mix(_mix(mid, light, tone * 0.6), dark, (1 - tone) * 0.35)
            # Rim light on the side facing the top left, shade on the other.
            facing = ((px - x) + (py - y)) / max(best, 1e-3) * 0.5
            rim = max(0.0, 1.0 - edge / 5.0)
            color = tuple(c * (1.0 + 0.28 * facing * rim) for c in base)
            n2 = _pnoise(x, y, 5, (size, size), seed + 3)
            color = tuple(c * (0.88 + 0.24 * n2) for c in color)
            if _pnoise(x, y, 14, (size, size), seed + 5) > 0.72:
                color = _mix(color, lichen, 0.45)
            limit = 2.2 if grout else 1.1
            if edge < limit:
                color = _mix(color, _mix(dark, (0, 0, 0), 0.4), 0.85 if grout else 0.7)
            canvas.px[y][x] = color


def paint(preset: str, size: int = 256, seed: int = 1, colors: dict | None = None) -> Image.Image:
    if preset not in PRESETS:
        raise ValueError(f"unknown texture preset {preset!r}; choose from {', '.join(PRESETS)}")
    recipe = PRESETS[preset]
    palette = {**recipe["colors"], **(colors or {})}
    rng = random.Random(f"texture:{preset}:{seed}")
    canvas = _Canvas(size)
    stops = [hex_to_rgb(palette[k]) for k in ("dark", "mid", "light")]
    if "cells" in recipe:
        _stones(canvas, rng, palette, recipe["cells"] * size / 256, recipe["grout"], seed)
        return canvas.image()
    _base(canvas, stops, seed, cell=size / 4, contrast=0.8 if "strokes" in recipe else 0.6)
    if recipe.get("grain"):
        _grain(canvas, rng, recipe["grain"])
    if recipe.get("sheen"):
        _sheen(canvas, palette, seed)
    if recipe.get("cracks"):
        _cracks(canvas, rng, palette, recipe["cracks"])
    if recipe.get("pebbles"):
        _pebbles(canvas, rng, palette, recipe["pebbles"])
    if recipe.get("strokes"):
        _strokes(canvas, rng, palette, recipe["strokes"])
    if recipe.get("flowers"):
        _flowers(canvas, rng, palette, recipe["flowers"])
    return canvas.image()


IMPORT_FILE = """[remap]

importer="texture"
type="CompressedTexture2D"

[params]

compress/mode=0
mipmaps/generate=true
"""
