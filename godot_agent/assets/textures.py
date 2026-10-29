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


# ── Foliage cards (RGBA) ──
#
# Painted the way hand-painted strategy-game foliage is painted: a dark, cool mass
# first, then mid tones, then warm, bright tips on top, so every card has depth before
# the model's vertex tint colours it (the foliage shader multiplies the card by the
# tint, so one grey card serves green, amber and red trees). Painted at four times the
# size and downscaled, so strokes are crisp without jaggies.
#
# The shader cuts alpha at 0.42. Each card keeps a clean alpha, plus a faint fill
# (below the cut) in the small gaps between strokes: invisible up close, it is what
# the mipmaps average into body further away, so distant foliage keeps its mass
# instead of thinning to nothing.

CARDS = ("foliage_pine", "foliage_leaves", "foliage_grass", "foliage_fern")
_SUPER = 4


def _tone(v: float) -> tuple:
    """Grey value v (0..1) as RGBA, a little cool in the shadows and warm in the light.

    Values are lifted on the way out (0.5 paints as 0.71): the shader multiplies the
    card with the tint in linear light, so a card whose middle tones sit near 0.75
    shows the tint itself there, its shadows darker and its tips brighter than it."""
    v = 1.0 - (1.0 - max(0.0, min(1.0, v))) ** 1.8
    w = (v - 0.55) * 2.0
    return (int(min(255.0, 255 * v * (1 + 0.05 * w))), int(min(255.0, 255 * v * (1 + 0.02 * w))),
            int(min(255.0, 255 * v * (1 - 0.12 * w))), 255)


def _bezier(p0, p1, p2, n: int) -> list:
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        pts.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return pts


def _ribbon(draw, pts: list, widths: list, values: list, split: float = 0.0) -> None:
    """A stroke along pts with a width and a grey value per point. A split lights the
    left half by that much and shades the right half: a folded blade, a lit leaflet."""
    n = len(pts)
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        ax, ay = pts[max(0, i - 1)]
        bx, by = pts[min(n - 1, i + 1)]
        length = math.hypot(bx - ax, by - ay) or 1.0
        nx, ny = -(by - ay) / length * widths[i] / 2, (bx - ax) / length * widths[i] / 2
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    for i in range(n - 1):
        v = (values[i] + values[i + 1]) / 2
        if split:
            draw.polygon([pts[i], left[i], left[i + 1], pts[i + 1]], fill=_tone(v + split))
            draw.polygon([pts[i], pts[i + 1], right[i + 1], right[i]], fill=_tone(v - split))
        else:
            draw.polygon([left[i], left[i + 1], right[i + 1], right[i]], fill=_tone(v))


def _needle(draw, x: float, y: float, angle: float, length: float, width: float, v0: float, v1: float,
            curve: float = 0.0, segments: int = 5) -> None:
    """A tapering needle or blade from (x, y), bowed sideways by `curve` (a fraction of
    its length), shading from v0 at its root to v1 at its tip."""
    ca, sa = math.cos(angle), math.sin(angle)
    mid = (x + ca * length * 0.5 - sa * curve * length, y + sa * length * 0.5 + ca * curve * length)
    pts = _bezier((x, y), mid, (x + ca * length, y + sa * length), segments)
    widths = [width * (1 - 0.82 * i / segments) for i in range(segments + 1)]
    values = [v0 + (v1 - v0) * (i / segments) ** 0.8 for i in range(segments + 1)]
    _ribbon(draw, pts, widths, values)


def _leaf_outline(base, angle: float, length: float, width: float, bow: float, rounded: float,
                  segments: int = 10) -> tuple:
    """Spine points, unit normals and half-widths of a leaf growing from `base`: widest a
    third of the way out, pointed (rounded=0) or blunt (rounded=1) at the tip."""
    ca, sa = math.cos(angle), math.sin(angle)
    tip_exp = 1.0 - 0.55 * rounded
    peak = (0.55 / (0.55 + tip_exp)) ** 0.55 * (tip_exp / (0.55 + tip_exp)) ** tip_exp
    spine, half = [], []
    for i in range(segments + 1):
        s = i / segments
        x, y = length * s, bow * length * math.sin(math.pi * s) * 0.5
        spine.append((base[0] + ca * x - sa * y, base[1] + sa * x + ca * y))
        half.append(width / 2 * (s ** 0.55) * ((1 - s) ** tip_exp) / peak)
    normals = []
    for i in range(segments + 1):
        ax, ay = spine[max(0, i - 1)]
        bx, by = spine[min(segments, i + 1)]
        length_i = math.hypot(bx - ax, by - ay) or 1.0
        normals.append((-(by - ay) / length_i, (bx - ax) / length_i))
    return spine, normals, half


def _paint_leaf(draw, base, angle: float, length: float, width: float, value: float, lit_left: bool,
                bow: float = 0.0, rounded: float = 0.0, contrast: float = 1.0) -> None:
    """A leaf in soft bands: darker at the stalk, lighter toward the tip, one side of the
    midrib in light and the other in shade, the lit edge catching a little more."""
    spine, normals, half = _leaf_outline(base, angle, length, width, bow, rounded)
    bands = (0.0, 0.18, 0.62, 1.0)
    for i in range(len(spine) - 1):
        s = (i + 0.5) / (len(spine) - 1)
        along = (-0.16 * (1 - s) ** 2 + 0.07 * s) * contrast
        for side in (1, -1):
            lit = (side == 1) == lit_left
            v_side = value + along + (0.07 if lit else -0.07) * contrast
            for k in range(3):
                f0, f1 = bands[k] * side, bands[k + 1] * side
                v = v_side + ((0.035, 0.0, 0.05 if lit else -0.035)[k]) * contrast
                quad = []
                for j, f in ((i, f0), (i, f1), (i + 1, f1), (i + 1, f0)):
                    quad.append((spine[j][0] + normals[j][0] * half[j] * f,
                                 spine[j][1] + normals[j][1] * half[j] * f))
                draw.polygon(quad, fill=_tone(v))


def _leaf_polygon(base, angle: float, length: float, width: float, bow: float, rounded: float) -> list:
    spine, normals, half = _leaf_outline(base, angle, length, width, bow, rounded)
    left = [(x + nx * h, y + ny * h) for (x, y), (nx, ny), h in zip(spine, normals, half)]
    right = [(x - nx * h, y - ny * h) for (x, y), (nx, ny), h in zip(spine, normals, half)]
    return left + right[::-1]


def _noise_image(size: int, cells: int, rng: random.Random, low: float, high: float) -> Image.Image:
    """Smooth value noise between low and high (0..1) as an 'L' image."""
    small = Image.new("L", (cells, cells))
    small.putdata([int(255 * rng.uniform(low, high)) for _ in range(cells * cells)])
    return small.resize((size, size), Image.BICUBIC)


def _shade_rgb(img: Image.Image, factor: Image.Image) -> Image.Image:
    """Multiplies the colour (not the alpha) by an 'L' factor image (255 = unchanged)."""
    from PIL import ImageChops
    alpha = img.getchannel("A")
    rgb = ImageChops.multiply(img.convert("RGB"), Image.merge("RGB", (factor, factor, factor)))
    rgb.putalpha(alpha)
    return rgb


def _mottle(img: Image.Image, rng: random.Random, broad: float = 0.12, fine: float = 0.06) -> Image.Image:
    """Painterly unevenness: broad patches of lighter and darker paint, then a finer grain."""
    from PIL import ImageChops
    size = img.width
    a = _noise_image(size, 6, rng, 1 - broad, 1.0)
    b = _noise_image(size, 26, rng, 1 - fine, 1.0)
    factor = ImageChops.multiply(a, b).point(lambda x: min(255, int(x * (1 + broad * 0.6))))
    return _shade_rgb(img, factor)


def _soften(img: Image.Image, radius: float) -> Image.Image:
    """Blends painted bands into soft gradients inside the shape, keeping its edge."""
    from PIL import ImageFilter
    alpha = img.getchannel("A")
    soft = img.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
    soft.putalpha(alpha)
    return soft


def _paint_pine(img: Image.Image, rng: random.Random) -> Image.Image:
    """A pine bough from above, root on the left: a twig with alternate side sprigs,
    each a bristly spray of long needles sweeping toward its own pointed tip, so the
    outline is a saw-tooth of needle tufts. Dark and cool in at the wood, warm and
    bright at the needle tips; the young tip of the bough is lit all the way across."""
    from PIL import ImageDraw
    big = img.width
    draw = ImageDraw.Draw(img)

    def twig(t):
        return (big * (0.01 + 0.95 * t), big * (0.5 + 0.012 * math.sin(math.pi * t)))

    def reach(t):  # half the bough's width at t, in pixels: slim at the root, pointed tip
        rise = 0.12 + 0.3 * math.sin(math.pi / 2 * min(1.0, t / 0.4))
        fall = 1.0 if t < 0.4 else 1 - ((t - 0.4) / 0.6) ** 1.3 * 0.86
        return big * rise * fall

    sprigs = []
    t, side = 0.05, rng.choice((-1, 1))
    while t < 0.84:
        angle = math.radians(rng.uniform(42, 56) - 10 * t)
        # Long enough for the tip to land on the bough's outline, a little short or long.
        length = reach(t) / math.sin(angle)
        for _ in range(6):
            t_tip = min(1.0, t + length * math.cos(angle) / (big * 0.95))
            length = 0.5 * length + 0.5 * reach(t_tip) / math.sin(angle)
        length = min(length * rng.uniform(0.8, 1.05), (big * 0.96 - twig(t)[0]) / math.cos(angle))
        x0, y0 = twig(t)
        tip = (x0 + math.cos(angle) * length, y0 + side * math.sin(angle) * length)
        bend = (x0 + math.cos(angle + 0.18) * length * 0.5, y0 + side * math.sin(angle + 0.18) * length * 0.5)
        sprigs.append({"side": side, "pts": _bezier((x0, y0), bend, tip, 12),
                       "shade": rng.uniform(-0.08, 0.08) + 0.12 * (t - 0.5)})
        side = -side
        t += rng.uniform(0.026, 0.034)
    # The twig carries needles too, longest toward the tip, where they form the end tuft.
    sprigs.append({"side": 0, "pts": [twig(i / 24) for i in range(25)], "shade": 0.04})

    def needles(sprig, start: float, scale: float):
        """(x, y, angle, length, s) of every needle along a sprig from `start` on."""
        pts, out = sprig["pts"], []
        n = len(pts) - 1
        main = sprig["side"] == 0
        total = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(n))
        count = max(4, int(total / (big * 0.0085)))
        for k in range(count):
            s = (k + rng.random() * 0.7) / count
            if s < start:
                continue
            f = s * n
            i = min(n - 1, int(f))
            x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * (f - i)
            y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * (f - i)
            heading = math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
            for flank in (-1, 1):
                if main:
                    spread = math.radians(rng.uniform(20, 48))
                    length = big * 0.1 * (0.5 + 0.5 * s) * rng.uniform(0.7, 1.15)
                else:
                    spread = math.radians(rng.uniform(16, 40)) * (1 - 0.35 * s)
                    length = big * 0.088 * (1 - 0.5 * s) * rng.uniform(0.7, 1.15)
                out.append((x, y, heading + flank * spread, length * scale, s))
        tail = pts[-1]
        heading = math.atan2(tail[1] - pts[-2][1], tail[0] - pts[-2][0])
        for _ in range(7):
            out.append((tail[0] - math.cos(heading) * big * 0.01, tail[1] - math.sin(heading) * big * 0.01,
                        heading + rng.uniform(-0.35, 0.35), big * 0.055 * rng.uniform(0.7, 1.25) * scale, 1.0))
        return out

    width = big * 0.0048
    # 1. The shadowed mass: thick, dark needles in close around the wood.
    for sprig in sprigs:
        for x, y, a, length, s in needles(sprig, 0.0, 0.75):
            if s > 0.7:
                continue
            _needle(draw, x, y, a, length, width * 2.2, 0.08, 0.22 + sprig["shade"] * 0.5,
                    curve=rng.uniform(-0.08, 0.08))
    # 2. The wood: the twig, then the sprigs.
    pts = [twig(i / 24) for i in range(25)]
    _ribbon(draw, pts, [big * (0.016 - 0.011 * i / 24) for i in range(25)],
            [0.22 + 0.12 * i / 24 for i in range(25)], split=0.05)
    for sprig in sprigs[:-1]:
        n = len(sprig["pts"])
        _ribbon(draw, sprig["pts"], [big * (0.0065 - 0.0045 * i / (n - 1)) for i in range(n)],
                [0.2 + 0.16 * i / (n - 1) for i in range(n)])
    # 3. Mid-tone needles, darker at their roots.
    for sprig in sprigs:
        for x, y, a, length, s in needles(sprig, 0.0, 1.0):
            v = sprig["shade"] + rng.uniform(-0.05, 0.05)
            _needle(draw, x, y, a, length, width, 0.18 + v, 0.52 + v + 0.16 * s, curve=rng.uniform(-0.1, 0.1))
    # 4. Lit needles over the outer part of every sprig and the end of the twig.
    for sprig in sprigs:
        for x, y, a, length, s in needles(sprig, 0.55 if sprig["side"] == 0 else 0.36, 0.78):
            v = sprig["shade"] + rng.uniform(-0.04, 0.04)
            _needle(draw, x, y, a, length, width * 0.9, 0.42 + v + 0.12 * s, 0.92 + v + 0.08 * s,
                    curve=rng.uniform(-0.1, 0.1))
    # 5. The freshest growth: short, bright needles at each sprig's tip.
    for sprig in sprigs:
        for x, y, a, length, s in needles(sprig, 0.82, 0.55):
            _needle(draw, x, y, a, length, width * 0.8, 0.62 + sprig["shade"], 0.98, curve=rng.uniform(-0.1, 0.1))
    return img


def _paint_leaves(img: Image.Image, rng: random.Random) -> Image.Image:
    """A full, rounded clump of broad leaves built from smaller bulging clumps: each one
    lit on its upper side and dark beneath, shadowing the clumps behind it, its leaves
    overlapping and each lit on one side of the midrib."""
    from PIL import ImageDraw, ImageFilter
    big = img.width
    light = (-0.3, -0.62, 0.72)
    cx, cy = 0.5, 0.52
    lobes = []
    count = rng.randint(5, 6)
    phase = rng.uniform(0, math.tau)
    for i in range(count):
        a = phase + i * math.tau / count + rng.uniform(-0.25, 0.25)
        r = rng.uniform(0.18, 0.22)
        d = min(rng.uniform(0.22, 0.27), 0.475 - r)
        lobes.append((cx + math.cos(a) * d, cy + math.sin(a) * d * 0.9, r))
    # Back to front: the upper clumps sit behind, the lower ones before them, the
    # central clump in front of all.
    lobes.sort(key=lambda lobe: lobe[1])
    lobes.append((cx + rng.uniform(-0.02, 0.02), cy + rng.uniform(-0.02, 0.02), rng.uniform(0.27, 0.3)))

    def shadow_of(polygons, offset, blur, strength):
        mask = Image.new("L", img.size, 0)
        mdraw = ImageDraw.Draw(mask)
        for poly in polygons:
            mdraw.polygon([(x + offset[0], y + offset[1]) for x, y in poly], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
        img.paste(_shade_rgb(img, mask.point(lambda m: 255 - int(m * strength))), (0, 0))

    for n, (lx, ly, lr) in enumerate(lobes):
        lx, ly, lr = lx * big, ly * big, lr * big
        # Global shading: the whole clump is a ball lit from above.
        gx, gy = (lx - cx * big) / (big * 0.42), (ly - cy * big) / (big * 0.42)
        lift = 0.12 * (-gy) - 0.04 * gx
        leaves = []
        total = int(36 * (lr / (big * 0.2)) ** 2)
        for k in range(total):
            backing = k < total // 4
            rr = lr * (0.55 * math.sqrt(rng.random()) if backing else 0.88 * rng.random() ** 0.5)
            theta = rng.uniform(0, math.tau)
            px, py = lx + rr * math.cos(theta), ly + rr * math.sin(theta)
            angle = (theta if rr > lr * 0.3 else rng.uniform(0, math.tau)) + rng.uniform(-0.8, 0.8)
            length = big * rng.uniform(0.12, 0.165)
            limit = lr * 0.8 if backing else lr * 1.1
            for _ in range(8):
                tip = (px + math.cos(angle) * length * 0.6, py + math.sin(angle) * length * 0.6)
                if (math.hypot(tip[0] - lx, tip[1] - ly) < limit and big * 0.015 < tip[0] < big * 0.985
                        and big * 0.015 < tip[1] < big * 0.985):
                    break
                length *= 0.88
            base = (px - math.cos(angle) * length * 0.4, py - math.sin(angle) * length * 0.4)
            nx, ny = (px - lx) / lr, (py - ly) / lr
            nz = math.sqrt(max(0.0, 1 - min(1.0, nx * nx + ny * ny)))
            lam = max(0.0, nx * light[0] + ny * light[1] + nz * light[2])
            value = 0.12 + 0.56 * lam + lift + rng.uniform(-0.08, 0.08)
            if backing:
                value = 0.12 + 0.1 * lam + lift * 0.5
            lit_left = (-math.sin(angle) * light[0] + math.cos(angle) * light[1]) > 0
            width = length * rng.uniform(0.5, 0.62)
            leaves.append({"base": base, "angle": angle, "length": length, "width": width,
                           "bow": rng.uniform(-0.18, 0.18), "value": value, "lit_left": lit_left,
                           "backing": backing, "depth": -1.0 if backing else nz + rng.uniform(-0.3, 0.3)})
        leaves.sort(key=lambda leaf: leaf["depth"])
        if n:
            # The clump shadows the ones already painted behind it.
            outline = [[(lx + math.cos(i / 24 * math.tau) * lr * 1.05, ly + math.sin(i / 24 * math.tau) * lr * 1.05)
                        for i in range(24)]]
            shadow_of(outline, (big * 0.015, big * 0.03), big * 0.03, 0.42)
        split = sum(1 for leaf in leaves if leaf["backing"])
        front = leaves[split:]
        tiers = [leaves[:split], front[:len(front) // 2], front[len(front) // 2:]]
        for t, tier in enumerate(tiers):
            if t:
                shadow_of([_leaf_polygon((leaf["base"][0] + big * 0.01, leaf["base"][1] + big * 0.02), leaf["angle"],
                                         leaf["length"], leaf["width"] * 1.1, leaf["bow"], 0.25) for leaf in tier],
                          (0, 0), big * 0.012, 0.55)
            draw = ImageDraw.Draw(img)
            for leaf in tier:
                _paint_leaf(draw, leaf["base"], leaf["angle"], leaf["length"], leaf["width"], leaf["value"],
                            leaf["lit_left"], bow=leaf["bow"], rounded=0.25,
                            contrast=0.6 if leaf["backing"] else 1.2)
    return _soften(img, big * 0.0035)


def _paint_grass(img: Image.Image, rng: random.Random) -> Image.Image:
    """A lush tuft: blades fanning up and out from the bottom centre, the outer ones
    arching over, each folded along its length (one half lit); dark and cool at the
    root, warm and light at the tip."""
    from PIL import ImageDraw
    big = img.width
    draw = ImageDraw.Draw(img)
    blades = []
    for k in range(92):
        short = k < 16
        offset = max(-0.15, min(0.15, rng.gauss(0, 0.06)))
        x0 = big * (0.5 + offset)
        lean = max(-1.15, min(1.15, math.radians(offset / 0.15 * 30 + rng.gauss(0, 20))))
        height = big * (rng.uniform(0.25, 0.45) if short else rng.uniform(0.5, 0.97)) * (1 - 0.28 * abs(lean))
        # Blades leaning out arch over under their own weight; upright ones barely bend.
        droop = math.copysign(math.radians(rng.uniform(10, 34) + 40 * abs(lean)), lean) if abs(lean) > 0.18 \
            else math.radians(rng.uniform(-20, 20))
        blades.append((rng.random() * (0.6 if short else 1.0), x0, lean, droop, height, short))
    blades.sort()
    for depth, x0, lean, droop, height, short in blades:
        for _ in range(12):
            p0 = (x0, big * 1.01)
            p1 = (x0 + math.sin(lean) * height * 0.55, p0[1] - math.cos(lean) * height * 0.55)
            p2 = (p1[0] + math.sin(lean + droop) * height * 0.5, p1[1] - math.cos(lean + droop) * height * 0.5)
            if big * 0.02 < p2[0] < big * 0.98 and big * 0.02 < p2[1] < big * 0.9 and big * 0.02 < p1[0] < big * 0.98:
                break
            height *= 0.9
        pts = _bezier(p0, p1, p2, 14)
        w0 = big * (rng.uniform(0.026, 0.036) if short else rng.uniform(0.017, 0.028))
        widths = [w0 * (1 - i / 14) ** 0.8 + big * 0.0045 for i in range(15)]
        v_root, v_tip = 0.08 + 0.08 * depth, 0.62 + 0.34 * depth
        values = [v_root + (v_tip - v_root) * (i / 14) ** 0.85 for i in range(15)]
        _ribbon(draw, pts, widths, values, split=0.07 if lean + droop * 0.5 < 0 else -0.07)
    # The heart of the tuft sits in its own shadow.
    shade = Image.linear_gradient("L").resize(img.size).point(
        lambda y: 255 - int(115 * max(0.0, (y / 255 - 0.62) / 0.38) ** 1.3))
    return _shade_rgb(img, shade)


def _paint_fern(img: Image.Image, rng: random.Random) -> Image.Image:
    """A fern frond from above, root on the left: a rachis with paired pinnae that sweep
    toward the tip and shrink to nothing there, each a comb of rounded pinnules."""
    from PIL import ImageDraw
    big = img.width
    draw = ImageDraw.Draw(img)

    def rachis(t):
        return (big * (0.015 + 0.955 * t), big * (0.5 + 0.016 * math.sin(math.pi * t * 0.9)))

    def envelope(t):
        grow = 0.62 + 0.38 * math.sin(math.pi / 2 * min(1.0, t / 0.3))
        return grow * (1.0 if t < 0.3 else (1 - (t - 0.3) / 0.7) ** 0.9 * 0.93 + 0.07)

    pinnae = []
    pairs = 14
    for k in range(pairs):
        t = 0.05 + 0.9 * (k / (pairs - 1)) ** 0.9
        for side in (-1, 1):
            tt = t + side * 0.009
            base = rachis(tt)
            length = big * 0.43 * envelope(tt) * rng.uniform(0.92, 1.05)
            angle = math.radians(rng.uniform(58, 66) - 16 * t)
            a0 = angle + 0.08
            a1 = angle - 0.3
            p1 = (base[0] + math.cos(a0) * length * 0.5, base[1] + side * math.sin(a0) * length * 0.5)
            p2 = (p1[0] + math.cos(a1) * length * 0.5, p1[1] + side * math.sin(a1) * length * 0.5)
            pinnae.append({"t": tt, "side": side, "length": length, "pts": _bezier(base, p1, p2, 10),
                           "shade": rng.uniform(-0.05, 0.05) + 0.1 * (t - 0.5)})
    # 1. Shadow under each pinna, so its pinnules sit on darkness.
    for pinna in pinnae:
        pts, n = pinna["pts"], len(pinna["pts"]) - 1
        reach = big * 0.05 * (pinna["length"] / (big * 0.43)) ** 0.5
        _ribbon(draw, pts, [reach * 1.3 * (1 - 0.75 * i / n) for i in range(n + 1)],
                [0.12 + 0.12 * i / n + pinna["shade"] * 0.5 for i in range(n + 1)])
    # 2. Pinnules: rounded leaflets angled toward the pinna's tip, lit on top.
    for pinna in pinnae:
        pts, n = pinna["pts"], len(pinna["pts"]) - 1
        reach = big * 0.05 * (pinna["length"] / (big * 0.43)) ** 0.5
        count = max(3, int(pinna["length"] / (big * 0.018)))
        for j in range(count):
            for flank in (-1, 1):
                s = min(0.97, (j + (0.25 if flank > 0 else 0.75)) / count)
                f = s * n
                i = min(n - 1, int(f))
                x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * (f - i)
                y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * (f - i)
                heading = math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
                size = reach * (1 - 0.68 * s) * rng.uniform(0.9, 1.08)
                angle = heading + flank * math.radians(rng.uniform(44, 56))
                value = 0.28 + 0.5 * s + pinna["shade"] + rng.uniform(-0.04, 0.04)
                # The flank facing the frond's tip catches the light.
                if flank * pinna["side"] < 0:
                    value += 0.06
                _paint_leaf(draw, (x, y), angle, size, size * 0.58, value, flank > 0, rounded=0.55,
                            contrast=0.9)
        # The pinna's tip: one last small leaflet.
        tail, prev = pts[-1], pts[-2]
        size = reach * 0.75
        tail = (tail[0] - (tail[0] - prev[0]) * 0.6, tail[1] - (tail[1] - prev[1]) * 0.6)
        _paint_leaf(draw, tail, math.atan2(tail[1] - prev[1], tail[0] - prev[0]), size, size * 0.4,
                    0.8 + pinna["shade"], True, rounded=0.0, contrast=0.9)
    # 3. Midribs, then the rachis on top.
    for pinna in pinnae:
        pts, n = pinna["pts"], len(pinna["pts"]) - 1
        _ribbon(draw, pts, [big * (0.0055 - 0.004 * i / n) for i in range(n + 1)],
                [0.36 + 0.24 * i / n + pinna["shade"] for i in range(n + 1)])
    pts = [rachis(i / 30) for i in range(31)]
    _ribbon(draw, pts, [big * (0.016 - 0.011 * i / 30) for i in range(31)],
            [0.34 + 0.24 * i / 30 for i in range(31)], split=0.06)
    return img


def _finish(img: Image.Image, size: int, gaps: float = 1.0) -> Image.Image:
    """Downscales a painted card and gives it a clean alpha: solid inside, cut outside,
    a faint below-the-cut fill in small gaps for the mipmaps (`gaps` scales how wide a
    gap it fills), and colour bled out past the edges so filtering never pulls in black."""
    from PIL import ImageChops, ImageFilter
    small = img.resize((size, size), Image.LANCZOS)
    alpha = small.getchannel("A").point(lambda a: 0 if a < 20 else 255 if a > 236 else round((a - 20) * 255 / 216))
    small.putalpha(alpha)
    solid = alpha.point(lambda a: 255 if a >= 128 else 0)
    # A round closing (grow, then shrink back) finds the small gaps between strokes.
    grow = ImageFilter.GaussianBlur(size / 100 * gaps)
    closed = solid.filter(grow).point(lambda a: 255 if a > 30 else 0).filter(grow).point(
        lambda a: 255 if a > 225 else 0)
    # Colour for every transparent pixel: the average of the paint nearby.
    premultiplied = small.convert("RGBa")
    bleed = Image.new("RGB", small.size, small.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0)))
    for radius in (size / 4, size / 16, size / 48, 1.2):
        blurred = premultiplied.filter(ImageFilter.GaussianBlur(radius))
        reached = blurred.split()[3].point(lambda a: 255 if a > 2 else 0)
        bleed = Image.composite(blurred.convert("RGBA").convert("RGB"), bleed, reached)
    # The gap fill is darker: at a distance the gaps read as shadow between strokes.
    shade = Image.new("L", small.size, 150)
    gaps = ImageChops.subtract(closed, solid)
    bleed = Image.composite(_shade_rgb(bleed.convert("RGBA"), shade).convert("RGB"), bleed, gaps)
    rgb = Image.composite(small.convert("RGB"), bleed, alpha.point(lambda a: 255 if a else 0))
    rgb.putalpha(ImageChops.lighter(alpha, gaps.point(lambda g: 82 if g else 0)))
    return rgb


def paint_card(preset: str, size: int = 256, seed: int = 1) -> Image.Image:
    """An alpha-cut foliage card in grey values: pine bough, leaf clump, grass tuft or
    fern frond. Boughs and fronds grow left to right (u runs root to tip, as gab.bough
    maps them); the grass tuft and leaf clump stand on the card's bottom edge."""
    if preset not in CARDS:
        raise ValueError(f"unknown card preset {preset!r}; choose from {', '.join(CARDS)}")
    rng = random.Random(f"card:{preset}:{seed}")
    painter = {"foliage_pine": _paint_pine, "foliage_leaves": _paint_leaves, "foliage_grass": _paint_grass,
               "foliage_fern": _paint_fern}[preset]
    img = _mottle(painter(Image.new("RGBA", (size * _SUPER, size * _SUPER), (0, 0, 0, 0)), rng), rng)
    # The fern's comb of pinnae is its silhouette: fill only the narrowest gaps there.
    return _finish(img, size, gaps=0.5 if preset == "foliage_fern" else 1.0)
