"""Painted UI frames: nine-slice panels, buttons, slots and framed icons, plus Godot StyleBoxes.

Strategy-game consoles get their weight from a few painted tricks, all done here:
a bevelled trim (gilt, iron or bronze) lit from the top left with a dark outline on
both sides, studs at the corners, a fill with material (stone, wood, leather,
slate) and an inner shadow where the fill meets the trim. Fills use periodic noise
whose period is the nine-slice centre, so Godot can tile the centre seamlessly
instead of stretching it.

The "console" preset paints a whole bottom console from a JSON spec instead: one
material (bronze over stone, lit as a height field) for every piece, see console().
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

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
PRESETS = ("panel", "bar", "tooltip", "button", "slot", "frame", "console")


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
    if preset == "console":
        raise ValueError("the console preset paints a folder of pieces: use write() or console()")
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
               seed: int = 1, edge: str | None = None, size: int | None = None, foot: float = 0.0) -> Image.Image:
    """A command-card icon: painted radial backdrop, the subject, a bevelled border.

    With `edge`, the command-button look of a strategy-game card instead: the subject cropped
    to fill the button, saturated, with a warm rim light and a contact shadow, over a glow
    from `background` at the centre to `edge` at the rim, a glassy sheen, an inner shadow and
    a bevelled border in the trim's metal; trim "hue" draws the border in the backdrop's own
    colours instead (a dark line and a light catch at the top left), so the subject is the
    brightest thing on the button. `foot` (a fraction of the height) keeps the bottom clear
    of the subject and shades it, so a cost written there sits on shadow. `size` box-filters
    the result down (render the model at 2x for crisp edges)."""
    if edge:
        return _command_icon(icon, trim, background, edge, border, seed, size or icon.width, foot)
    if trim == "hue":
        raise ValueError('trim "hue" is for the command-button look: give an edge colour too')
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


def _command_icon(icon: Image.Image, trim: str, centre: str, edge: str, border: int, seed: int,
                  size: int, foot: float = 0.0) -> Image.Image:
    n = icon.width
    k = n / size  # painting scale
    hue = trim == "hue"
    line = max(1, round(size / 54))  # the hue border: one pixel of a 54 px button
    b = round((line if hue else border) * k)
    rng = random.Random(seed)
    grid = Image.new("L", (6, 6))
    grid.putdata([rng.randrange(256) for _ in range(36)])
    mottle = grid.resize((n, n), Image.BICUBIC).load()
    c, e = hex_to_rgb(centre), hex_to_rgb(edge)
    base = Image.new("RGBA", (n, n))
    px = base.load()
    for y in range(n):
        for x in range(n):
            t = min(1.0, math.hypot((x / n - 0.46) * 1.05, y / n - 0.4) / 0.72) ** 1.25
            col = _mix(c, e, t)
            f = 0.9 + 0.2 * mottle[x, y] / 255.0
            px[x, y] = (*(int(max(0, min(255, v * f))) for v in col), 255)
    # a soft sheen across the top, the glass of the button
    sheen = Image.new("L", (n, n), 0)
    ImageDraw.Draw(sheen).ellipse([-n * 0.2, -n * 0.55, n * 1.2, n * 0.32], fill=255)
    layer = Image.new("RGBA", (n, n), (255, 240, 210, 0))
    layer.putalpha(sheen.filter(ImageFilter.GaussianBlur(8 * k)).point(lambda v: int(v * 0.13)))
    base.alpha_composite(layer)
    # the subject, cropped to fill the button above its foot
    inner = n - 2 * b
    foot_px = round(foot * n)
    top = b + round(3 * k)
    bottom = n - foot_px if foot_px else n - b - round(4 * k)
    bbox = icon.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if bbox:
        from PIL import ImageEnhance
        crop = icon.crop(bbox)
        box_w = int(inner * 0.86)
        box_h = min(box_w, bottom - top)
        scale = min(box_w / crop.width, box_h / crop.height)
        sub = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.LANCZOS)
        alpha = sub.getchannel("A")
        rgb = ImageEnhance.Color(sub.convert("RGB")).enhance(1.25)
        rgb = ImageEnhance.Brightness(ImageEnhance.Contrast(rgb).enhance(1.1)).enhance(1.06)
        sub = rgb.convert("RGBA")
        sub.putalpha(alpha)
        # rim light where the subject faces the top-left light
        shifted = Image.new("L", alpha.size, 0)
        shifted.paste(alpha, (round(1.5 * k), round(1.5 * k)))
        rim = Image.new("RGBA", alpha.size, (255, 236, 196, 0))
        rim.putalpha(ImageChops.subtract(alpha, shifted).filter(ImageFilter.GaussianBlur(0.4 * k))
                     .point(lambda v: int(v * 0.5)))
        sub.alpha_composite(rim)
        sub.putalpha(alpha)
        x = (n - sub.width) // 2
        if foot_px or sub.height > (bottom - top) * 0.8:
            y = bottom - sub.height
        else:
            y = top + (bottom - top - sub.height) // 2 + round(2 * k)
        sa = Image.new("L", (n, n), 0)
        sa.paste(alpha, (x + round(2 * k), y + round(3 * k)))
        shadow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        shadow.putalpha(sa.filter(ImageFilter.GaussianBlur(2.5 * k)).point(lambda v: int(v * 0.6)))
        base.alpha_composite(shadow)
        base.alpha_composite(sub, (x, y))
    if foot_px:
        # the foot: shade deepening to the bottom edge, under the cost
        reach = round(foot_px * 1.35)
        shade = Image.new("L", (n, n), 0)
        sp = shade.load()
        for yy in range(n - reach, n):
            v = int(255 * 0.55 * _smooth((yy - (n - reach) + 0.5) / reach) ** 0.9)
            for xx in range(n):
                sp[xx, yy] = v
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        layer.putalpha(shade)
        base.alpha_composite(layer)
    # inner shadow under the border's top and left
    pad = round(20 * k)
    mask = Image.new("L", (n + 2 * pad, n + 2 * pad), 255)
    ImageDraw.Draw(mask).rectangle([pad + b + round(2 * k), pad + b + round(3 * k), pad + n - b + round(2 * k),
                                    pad + n - b + round(3 * k)], fill=0)
    layer = Image.new("RGBA", (n, n), (4, 3, 2, 0))
    layer.putalpha(mask.filter(ImageFilter.GaussianBlur(3 * k)).crop((pad, pad, pad + n, pad + n))
                   .point(lambda v: int(v * 0.75)))
    base.alpha_composite(layer)
    px = base.load()
    if hue:
        # a dark line in the backdrop's own hue, and inside it a light catch at the top and left
        dark = tuple(v * 0.3 for v in e)
        catch = _mix(c, (255, 244, 224), 0.5)
        for y in range(n):
            for x in range(n):
                d = min(x, y, n - 1 - x, n - 1 - y)
                if d < b:
                    px[x, y] = (*(int(v) for v in dark), 255)
                elif d < 2 * b and (x < 2 * b or y < 2 * b) and x < n - b and y < n - b:
                    old = px[x, y]
                    px[x, y] = (*(int(old[i] + (catch[i] - old[i]) * 0.55) for i in range(3)), 255)
    else:
        # the bevelled border: lit top and left, shaded bottom and right, dark outline both sides
        stops = [hex_to_rgb(v) for v in TRIMS[trim]]
        light = [stops[2], _mix(stops[1], stops[2], 0.4), _mix(stops[0], stops[1], 0.75)]
        dark = [_mix(stops[1], stops[0], 0.15), _mix(stops[0], stops[1], 0.5), stops[0]]
        one = max(1, round(k))
        for y in range(n):
            for x in range(n):
                d = min(x, y, n - 1 - x, n - 1 - y)
                if d >= b:
                    continue
                if d < one:
                    px[x, y] = (10, 7, 5, 255)
                    continue
                t = (d - one) / max(1, b - one - 1)
                lit = (x + y) < n and (y == d or x == d)
                if (x == d and y > d and x + y > n - 1) or (y == d and x > n - 1 - d):
                    lit = (x + y) < n
                ramp = light if lit else dark
                col = _mix(ramp[0], ramp[2], min(1.0, t))
                if d >= b - one // 2 - (1 if one == 1 else 0) and b > 2 * one:
                    col = (14, 10, 7)
                px[x, y] = (*(int(v) for v in col), 255)
    if size != n:
        base = base.convert("RGBa").resize((size, size), Image.BOX).convert("RGBA")
    return base


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
    """Writes the PNG(s) and StyleBoxTexture(s) for a preset; buttons get one per state. The
    console preset treats dest as a folder and paints every piece its `spec` describes."""
    if preset == "console":
        return console(options.get("spec"), dest, res_base)
    options.pop("spec", None)
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


# ── Console: a strategy-game console in one material ──
#
# The preset "console" paints every piece of a Warcraft-style bottom console from one
# spec (a JSON file kept in the game): two wings with a recessed well each (minimap,
# command card), a centre piece whose top rises into an arch round a transparent
# portrait window, the low rail that joins them, a resource strip hanging from the top
# right, an end-of-game plate, a banner backing and the small nine-slices (tooltip,
# button states, a hover glow). Every piece is a silhouette turned into a height field
# and lit as weathered bronze over stone from the top left. One bronze rim follows the
# silhouette, meeting curves in round fillets; the wells are cut into the stone and
# read by their shade alone; the portrait window, the focal point, gets the one
# moulding. The bronze is brighter on runs that face the light and darker on those
# turned away, with patina in the crevices and inside corners and a two-tone
# specular; the stone is lit from under the rim and mottled at two scales. `scale`
# paints HD art (2: twice the texels of the layout) for the HUD to draw at half size.
# layout.json, written beside the pieces, gives the HUD the rectangles of the wells,
# the window and the text area in layout pixels, so code and art cannot drift.

CONSOLE = {
    "seed": 7,
    "scale": 1,
    "rail": 28,
    "shadow": 14,
    "rim": 6,
    "grooves": [],
    "fillet": 10,
    "socket": {"wall": 4, "bead": 0, "depth": 5.0},
    "wing": {"size": [240, 140], "slope": [178, 226], "well": [16, 14, 168, 112]},
    "card": {"socket": 54, "gap": [3, 4]},
    "centre": {
        "width": 572,
        "height": 184,
        "crest": {"x": 116, "radius": 86, "base": 80, "rise": [8, 34], "leaves": True},
        "window": [54, 14, 124, 128],
        "moulding": 10,
        "shoulder": [200, 534, 106],
        "text": [214, 8, 308, 84],
        "incised": 0,
    },
    "strip": {"height": 34, "cap": 44, "period": 64},
    "plate": [460, 200],
    "banner": [760, 96],
    "button": [96, 40],
    "tooltip": [64, 64],
    "portrait": {"centre": "#2a2620", "edge": "#0e0c0a", "key": "#30291f"},
    "material": {
        "dark": "#1c130c", "mid": "#604428", "worn": "#d6a464", "patina": "#30463c",
        "stone_dark": "#1b1816", "stone": "#403932", "spec": "#ffdaa0", "hollow": "#0b0908",
        "ember": "#ffb054",
    },
}
_S = 2  # painting scale: twice the output scale, box-filtered down
_LIGHT = (-0.42, -0.78, 0.46)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


_L = _unit(_LIGHT)
_H = _unit((_L[0], _L[1], _L[2] + 1.0))
_L2 = _unit((_L[0], _L[1]))  # the light's direction across the screen


def _blur(f, w, h, r, passes=3):
    """Separable box blur on a flat float list; three passes approximate a gaussian, edges clamp."""
    if r <= 0:
        return list(f)
    n = 2 * r + 1
    for _ in range(passes):
        out = [0.0] * (w * h)
        for y in range(h):
            row = y * w
            s = f[row] * (r + 1) + sum(f[row + min(k, w - 1)] for k in range(1, r + 1))
            for x in range(w):
                out[row + x] = s / n
                s += f[row + min(x + r + 1, w - 1)] - f[row + max(x - r, 0)]
        f = out
        if h == 1:
            continue
        out = [0.0] * (w * h)
        for x in range(w):
            s = f[x] * (r + 1) + sum(f[min(k, h - 1) * w + x] for k in range(1, r + 1))
            for y in range(h):
                out[y * w + x] = s / n
                s += f[min(y + r + 1, h - 1) * w + x] - f[max(y - r, 0) * w + x]
        f = out
    return f


def _inside(m, w, h):
    """Chamfer distance from each inside pixel to the nearest outside one. The canvas border
    is not an edge: shapes may run off the canvas (the screen edge) without a bevel."""
    inf = 1e9
    d = [0.0 if v < 0.5 else inf for v in m]
    a, b = 1.0, 1.4142
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            v = d[i]
            if v == 0.0:
                continue
            if x > 0 and d[i - 1] + a < v:
                v = d[i - 1] + a
            if y > 0:
                j = i - w
                if d[j] + a < v:
                    v = d[j] + a
                if x > 0 and d[j - 1] + b < v:
                    v = d[j - 1] + b
                if x < w - 1 and d[j + 1] + b < v:
                    v = d[j + 1] + b
            d[i] = v
    for y in range(h - 1, -1, -1):
        row = y * w
        for x in range(w - 1, -1, -1):
            i = row + x
            v = d[i]
            if v == 0.0:
                continue
            if x < w - 1 and d[i + 1] + a < v:
                v = d[i + 1] + a
            if y < h - 1:
                j = i + w
                if d[j] + a < v:
                    v = d[j] + a
                if x < w - 1 and d[j + 1] + b < v:
                    v = d[j + 1] + b
                if x > 0 and d[j - 1] + b < v:
                    v = d[j - 1] + b
            d[i] = v
    return d


def _px(img):
    return list(img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata())


def _field(img):
    return [v / 255.0 for v in _px(img)]


def _noise(w, h, cell, seed, period_x=None):
    """Smooth value noise in [0, 1] from an upscaled random grid; repeats every period_x."""
    rng = random.Random(seed)
    if period_x:
        gw, gh = max(1, round(period_x / cell)), h // cell + 4
        grid = Image.new("L", (gw, gh))
        grid.putdata([rng.randrange(256) for _ in range(gw * gh)])
        tiled = Image.new("L", (gw * 3, gh))
        for k in range(3):
            tiled.paste(grid, (k * gw, 0))
        big = tiled.resize((period_x * 3, gh * cell), Image.BICUBIC)
        strip = big.crop((period_x, cell, period_x * 2, cell + h))
        img = Image.new("L", (w, h))
        for x in range(0, w, period_x):
            img.paste(strip, (x, 0))
    else:
        gw, gh = w // cell + 4, h // cell + 4
        grid = Image.new("L", (gw, gh))
        grid.putdata([rng.randrange(256) for _ in range(gw * gh)])
        img = grid.resize((gw * cell, gh * cell), Image.BICUBIC).crop((cell, cell, cell + w, cell + h))
    return _field(img)


def _octaves(w, h, seed, period_x=None, base=40, octaves=4):
    total = [0.0] * (w * h)
    amp, norm, cell = 1.0, 0.0, base
    for o in range(octaves):
        n = _noise(w, h, max(2, int(cell)), seed * 31 + o, period_x)
        for i in range(w * h):
            total[i] += n[i] * amp
        norm += amp
        amp *= 0.5
        cell /= 2
    return [v / norm for v in total]


def _smooth(t):
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return t * t * (3 - 2 * t)


def _ogee(x, x0, x1, h0, h1):
    return h0 + (h1 - h0) * _smooth((x - x0) / (x1 - x0))


def _fillet(cx, base, radius, line, f, side):
    """Height on the arc of radius f that rounds the inside corner where an arch (a circle of
    `radius` round (cx, base)) meets a flat at height `line`, on side -1 (left) or 1 (right);
    -inf off the arc. Heights are measured up from the bottom edge."""
    dy = line + f - base
    reach = (radius + f) ** 2 - dy * dy
    if f <= 0 or line <= base or reach <= 0:
        return lambda x: -math.inf
    xf = cx + side * math.sqrt(reach)
    xt = cx + (xf - cx) * radius / (radius + f)
    lo, hi = min(xt, xf), max(xt, xf)

    def arc(x):
        if lo <= x <= hi:
            return line + f - math.sqrt(max(0.0, f * f - (x - xf) ** 2))
        return -math.inf
    return arc


def _profile_mask(width, height, top, ext=40):
    """Mask of everything below top(x) (height above the bottom edge, 1x units), antialiased."""
    k = 4 * _S
    img = Image.new("L", (width * k, height * k), 0)
    points, x = [], -ext
    while x <= width + ext:
        points.append((x * k, (height - top(x)) * k))
        x += 0.25
    points += [((width + ext) * k, (height + ext) * k), (-ext * k, (height + ext) * k)]
    ImageDraw.Draw(img).polygon(points, fill=255)
    return img.resize((width * _S, height * _S), Image.BOX)


def _shape_mask(width, height, draw_fn):
    """Mask drawn by draw_fn(draw, k) in 1x units multiplied by k, antialiased."""
    k = 4 * _S
    img = Image.new("L", (width * k, height * k), 0)
    draw_fn(ImageDraw.Draw(img), k)
    return img.resize((width * _S, height * _S), Image.BOX)


def _arch(draw, k, x, bottom, w, h, canvas_h, fill=255):
    """An arch-topped window: straight sides, a half circle of radius w/2 on top."""
    r = w / 2
    top = canvas_h - (bottom + h - r)
    draw.rectangle([x * k, top * k, (x + w) * k, (canvas_h - bottom) * k], fill=fill)
    draw.ellipse([x * k, (top - r) * k, (x + w) * k, (top + r) * k], fill=fill)


def _rrect(draw, k, x, y, w, h, r, fill=255):
    draw.rounded_rectangle([x * k, y * k, (x + w) * k, (y + h) * k], radius=max(0, r) * k, fill=fill)


def _leaf_points(cx, cy, length, width, angle, lobes=3):
    """Outline of a pointed oak leaf, its tip at `angle` (radians) from the stem at (cx, cy):
    widest a third of the way along, pointed at both ends, the edge waved into lobes."""
    pts = []
    for i in range(81):
        t = i / 80
        half = math.sin(math.pi * t ** 0.8) ** 1.25 * width / 2
        if lobes:
            half *= 1 + 0.16 * math.sin(math.pi * t) * math.cos(t * math.pi * 2 * lobes)
        pts.append((t * length, half))
    pts += [(p[0], -p[1]) for p in reversed(pts)]
    ca, sa = math.cos(angle), math.sin(angle)
    return [(cx + a * ca - b * sa, cy + a * sa + b * ca) for a, b in pts]


def _leaf(draw, k, cx, cy, length, width, angle, fill=255):
    draw.polygon([(x * k, y * k) for x, y in _leaf_points(cx, cy, length, width, angle)], fill=fill)


def _leaf_rib(draw, k, cx, cy, length, width, angle, fill=255):
    """The leaf's midrib: a line from the stem most of the way to the tip."""
    ca, sa = math.cos(angle), math.sin(angle)
    end = (cx + length * 0.86 * ca, cy + length * 0.86 * sa)
    draw.line([(cx + 2 * ca) * k, (cy + 2 * sa) * k, end[0] * k, end[1] * k], fill=fill,
              width=max(1, round(width * k)))


class _Relief:
    """A silhouette plus relief, lit as weathered bronze over stone."""

    def __init__(self, width, height, mask, seed, material, period_x=None, band=6.0, grooves=()):
        self.w, self.h = width * _S, height * _S
        self.width, self.height = width, height
        self.mask = _field(mask)
        self.seed = seed
        self.m = material
        self.period_x = period_x * _S if period_x else None
        self.wells = []  # (field, depth, colour, window, wall)
        self.ornament = [0.0] * (self.w * self.h)
        self.bronze = [0.0] * (self.w * self.h)
        self.gems = []
        self.grooves = list(grooves)
        self.band = float(band)  # width of the bronze rim along the silhouette; stone inside it

    def add_well(self, mask_img, depth=6.0, color=None, window=False, wall=2.2):
        self.wells.append((_field(mask_img), depth, color or self.m["hollow"], window, wall))

    def add_ornament(self, mask_img, height=2.6, soften=1, bronze=True):
        f = _field(mask_img)
        if soften:
            f = _blur(f, self.w, self.h, soften, passes=1)
        for i, v in enumerate(f):
            self.ornament[i] += v * height * _S
            if bronze and v > self.bronze[i]:
                self.bronze[i] = v

    def add_bronze(self, mask_img):
        for i, v in enumerate(_field(mask_img)):
            if v > self.bronze[i]:
                self.bronze[i] = v

    def add_frame(self, mask_img, width, height=3.0):
        """One rounded bronze moulding `width` wide round the outside of a shape (the
        portrait window): it rises from the opening, crowns half way and falls to the rim."""
        f = _field(mask_img)
        out = _inside([1.0 - v for v in f], self.w, self.h)
        wpx = width * _S
        for i, d in enumerate(out):
            if 0.0 < d < wpx + 2 * _S:
                t = min(1.0, d / wpx)
                self.ornament[i] += height * _S * math.sin(math.pi * t) ** 0.55
                edge = 1.0 - _smooth((d - wpx) / (1.4 * _S) + 0.5)
                if edge > self.bronze[i]:
                    self.bronze[i] = edge

    def add_gem(self, x, y, r):
        self.gems.append((x * _S, y * _S, r * _S))

    def add_socket(self, x, y, w, h, radius=4, wall=4, bead=0, depth=5.0):
        """A well cut into the stone for a widget drawn over it (minimap, command card): its
        walls fall into shade at the top and left and catch the light on the lower lip. A
        bead > 0 rings it in bronze. (x, y, w, h) is the content rectangle in 1x pixels."""
        W, H = self.width, self.height
        self.add_well(_shape_mask(W, H, lambda d, k: _rrect(d, k, x - wall, y - wall, w + 2 * wall, h + 2 * wall,
                                                            radius + wall)), depth=depth, wall=wall)
        if bead <= 0:
            return

        def ring(d, k):
            _rrect(d, k, x - wall - bead, y - wall - bead, w + 2 * (wall + bead), h + 2 * (wall + bead),
                   radius + wall + bead)
            _rrect(d, k, x - wall, y - wall, w + 2 * wall, h + 2 * wall, radius + wall, fill=0)

        self.add_ornament(_shape_mask(W, H, ring), height=1.8)

    def paint(self):
        w, h, n, m = self.w, self.h, self.w * self.h, self.m
        dist = _inside(self.mask, w, h)
        bevel_r = 6.0 * _S
        band = self.band * _S
        height = [0.0] * n
        bronze = [0.0] * n
        for i in range(n):
            d = dist[i]
            t = min(d / bevel_r, 1.0)
            hv = bevel_r * 0.9 * math.sqrt(max(0.0, 1 - (1 - t) ** 2))
            b = 1.0 - _smooth((d - band) / (1.6 * _S) + 0.5)
            # the rim's inner edge: a chamfer down to the stone
            hv -= (1 - b) * 2.6 * _S
            orn = self.ornament[i]
            bronze[i] = max(b, self.bronze[i])
            if bronze[i] < 0.5:
                for g in self.grooves:
                    e = (d - g * _S) / (0.8 * _S)
                    if -3 < e < 3:
                        hv -= 1.2 * _S * math.exp(-e * e)
            height[i] = hv + orn
        well_any = [0.0] * n
        window = [0.0] * n
        well_color = [None] * n
        for wf, depth, color, is_window, wall in self.wells:
            wd = _inside([1.0 if v >= 0.5 else 0.0 for v in wf], w, h)
            for i in range(n):
                if wf[i] > 0.02:
                    t = min(wd[i] / (wall * _S), 1.0)
                    height[i] -= depth * _S * _smooth(t) + depth * _S * 0.25 * wf[i]
                    if wf[i] > well_any[i]:
                        well_any[i] = wf[i]
                        well_color[i] = color
                    if is_window:
                        window[i] = max(window[i], wf[i])
        height = _blur(height, w, h, max(1, _S // 2), passes=2)
        broad = _blur(height, w, h, 3 * _S, passes=2)

        # Which way the silhouette faces, from a blurred mask: runs that face the light are
        # brighter, and the inside corners (more of the piece round them than along a
        # straight edge) gather patina.
        soft = _blur(self.mask, w, h, 4 * _S, passes=2)
        corner_r = 4 * _S
        wide = _blur(self.mask, w, h, corner_r, passes=2)
        line = max(8, int(corner_r * 4))
        step = _blur([0.0] * line + [1.0] * line, 2 * line, 1, corner_r, passes=2)
        ytop = [h] * w
        for x in range(w):
            for y in range(h):
                if self.mask[y * w + x] >= 0.5:
                    ytop[x] = y
                    break

        tone = _octaves(w, h, self.seed, self.period_x, base=36 * _S)
        grain = _noise(w, h, 2 * _S, self.seed + 5, self.period_x)
        grain2 = _noise(w, h, 1 * _S, self.seed + 6, self.period_x)
        blotch = _octaves(w, h, self.seed + 9, self.period_x, base=14 * _S, octaves=2)
        mottle = _octaves(w, h, self.seed + 13, self.period_x, base=64 * _S, octaves=2)
        streak = _noise(w, h, 22 * _S, self.seed + 17, self.period_x)

        glow = [0.0] * n  # warm light from a lantern gem
        for gx, gy, gr in self.gems:
            for y in range(h):
                for x in range(w):
                    glow[y * w + x] += math.exp(-max(0.0, math.hypot(x - gx, (y - gy) * 1.15) - gr) / (16.0 * _S))

        dark, mid, worn, patina = m["dark"], m["mid"], m["worn"], m["patina"]
        sdark, stone, spec, ember = m["stone_dark"], m["stone"], m["spec"], m["ember"]
        rgba = [(0, 0, 0, 0)] * n
        for y in range(h):
            for x in range(w):
                i = y * w + x
                a = self.mask[i]
                if a <= 0.0:
                    continue
                hl = height[i - 1] if x > 0 else height[i]
                hr = height[i + 1] if x < w - 1 else height[i]
                hu = height[i - w] if y > 0 else height[i]
                hd = height[i + w] if y < h - 1 else height[i]
                nx, ny = (hl - hr) * 0.5, (hu - hd) * 0.5
                inv = 1.0 / math.sqrt(nx * nx + ny * ny + 1.0)
                nx, ny, nz = nx * inv, ny * inv, inv
                dif = max(0.0, nx * _L[0] + ny * _L[1] + nz * _L[2])
                ndh = max(0.0, nx * _H[0] + ny * _H[1] + nz * _H[2])
                cav = min(1.0, max(0.0, (broad[i] - height[i]) / (2.0 * _S)))
                vex = min(1.0, max(0.0, (height[i] - broad[i]) / (1.4 * _S)))
                t = tone[i] * 0.75 + grain[i] * 0.25
                b = bronze[i]
                # facing of the silhouette here: 1 toward the light, -1 away from it
                gx = soft[i + 1] - soft[i - 1] if 0 < x < w - 1 else 0.0
                gy = soft[i + w] - soft[i - w] if 0 < y < h - 1 else 0.0
                gm = math.hypot(gx, gy)
                facing = 0.0
                if gm > 1e-5:
                    facing = (-gx * _L2[0] - gy * _L2[1]) / gm * min(1.0, gm * 6 * _S)
                d = dist[i]
                corner = 0.0
                if d < band + 4 * _S:
                    ref = step[min(2 * line - 1, line + max(0, int(d)) - 1)]
                    corner = min(1.0, max(0.0, (wide[i] - ref) * 4.0))
                # bronze: dark, mottled, streaked along its length, patina in the crevices
                # and inside corners, worn bright on the edges
                lit_run = 1.0 + 0.24 * facing
                metal = [(dark[c] + (mid[c] - dark[c]) * (0.3 + 0.7 * t)) * (0.86 + 0.28 * streak[i]) * lit_run
                         for c in range(3)]
                pat = min(1.0, cav * 0.9 + corner * 0.9 + max(0.0, blotch[i] - 0.6) * 1.2)
                metal = [metal[c] + (patina[c] - metal[c]) * pat * 0.55 for c in range(3)]
                wear = vex * (0.6 + 0.4 * grain[i]) * (0.75 + 0.35 * max(0.0, facing))
                metal = [metal[c] + (worn[c] - metal[c]) * min(1.0, wear * 0.8) for c in range(3)]
                # stone: cool, granular, matte; a soft shadow under the rim, then lit from
                # above and falling darker toward the base, mottled at a large scale
                g2 = 0.55 * tone[i] + 0.3 * grain[i] + 0.15 * grain2[i]
                rock = [sdark[c] + (stone[c] - sdark[c]) * g2 for c in range(3)]
                under = math.exp(-max(0.0, d - band) / (4.0 * _S)) * (0.75 + 0.25 * max(0.0, facing))
                below = (y - ytop[x]) / _S - self.band
                lift = 0.2 * math.exp(-max(0.0, below - 4.0) / 22.0) if below > 0 else 0.0
                above = (h - y) / _S
                lit = (1 - 0.6 * under) * (0.78 + 0.22 * _smooth(above / 110.0)) * (1 + lift)
                lit *= 0.86 + 0.28 * mottle[i]
                rock = [v * lit for v in rock]
                base = [rock[c] + (metal[c] - rock[c]) * b for c in range(3)]
                shade = 0.36 + 1.1 * dif
                ao = 1.0 - 0.5 * cav - 0.25 * corner * b
                gloss = b * (0.3 + 0.7 * vex) * (0.7 + 0.5 * max(0.0, facing)) + (1 - b) * 0.04
                # two speculars: a broad warm sheen and a tight bright glint
                col = [base[c] * shade * ao + (worn[c] * 0.3 * ndh ** 9 + spec[c] * ndh ** 60) * gloss
                       for c in range(3)]
                if glow[i] > 0.001:
                    facing_z = max(0.15, nz)
                    col = [col[c] + ember[c] * glow[i] * 0.35 * facing_z * (base[c] / 120 + 0.25) for c in range(3)]
                if well_any[i] > 0.02 and window[i] < 0.5:
                    inner = [well_color[i][c] * (0.8 + 0.4 * grain[i]) for c in range(3)]
                    k2 = _smooth((well_any[i] - 0.5) * 4) if well_any[i] < 0.75 else 1.0
                    # the walls keep their lighting, the floor goes dark
                    depth_t = min(1.0, max(0.0, (-height[i] - 2.5 * _S) / (2.5 * _S)))
                    col = [col[c] + (inner[c] - col[c]) * k2 * depth_t for c in range(3)]
                alpha = a * (1 - window[i]) if window[i] > 0.0 else a
                rgba[i] = (int(max(0, min(255, col[0]))), int(max(0, min(255, col[1]))),
                           int(max(0, min(255, col[2]))), int(max(0, min(255, alpha * 255))))
        img = Image.new("RGBA", (w, h))
        img.putdata(rgba)

        if any(wl[3] for wl in self.wells):
            # Inside a transparent window the frame's lip shades the top and left.
            win = Image.new("L", (w, h))
            win.putdata([int(v * 255) for v in window])
            shifted = Image.new("L", (w, h), 255)
            shifted.paste(win.point(lambda v: 255 - v), (2 * _S, 4 * _S))
            soft_win = shifted.filter(ImageFilter.GaussianBlur(5 * _S))
            inner = [int(min(255, s * 0.9) * (wv / 255.0)) for s, wv in zip(_px(soft_win), _px(win))]
            shade = Image.new("RGBA", (w, h), (8, 6, 4, 0))
            shade.putalpha(Image.frombytes("L", (w, h), bytes(inner)))
            img = Image.alpha_composite(shade, img)

        # Contact shadow onto the world round the silhouette.
        solid = Image.new("L", (w, h))
        solid.putdata([int(mv * (1 - wv) * 255) for mv, wv in zip(self.mask, window)])
        sh = Image.new("L", (w, h), 0)
        sh.paste(solid, (0, 1 * _S))
        sh = sh.filter(ImageFilter.GaussianBlur(5 * _S)).point(lambda v: int(v * 0.62))
        shadow = Image.new("RGBA", (w, h), (6, 4, 2, 0))
        shadow.putalpha(sh)
        img = Image.alpha_composite(shadow, img)
        for gx, gy, gr in self.gems:
            img = _gem(img, gx, gy, gr)
        return img


def _down(img, width, height):
    if img.size == (width, height):
        return img
    return img.convert("RGBa").resize((width, height), Image.BOX).convert("RGBA")


def _gem(img, gx, gy, gr):
    """An amber cabochon in a dark bezel: deep at the bottom, a bright core, a cut glint at
    the top left and warm light bounced along the lower rim. The one hot colour on the
    console, so its halo stays small."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = layer.load()
    ry = 1.25
    bezel = 0.32
    reach = 2.6
    deep, amber, core, hot = (120, 36, 6), (238, 128, 28), (255, 208, 110), (255, 248, 228)
    for y in range(int(gy - gr * ry * reach), int(gy + gr * ry * reach) + 2):
        for x in range(int(gx - gr * reach), int(gx + gr * reach) + 2):
            if not (0 <= x < w and 0 <= y < h):
                continue
            dx, dy = (x + 0.5 - gx) / gr, (y + 0.5 - gy) / (gr * ry)
            d = math.hypot(dx, dy)
            edge_px = (1.0 - d) * gr
            if d <= 1.0 + 0.5 / gr:
                v = (dy + 1) / 2  # 0 at the top, 1 at the bottom
                c = _mix(amber, deep, _smooth(v * 1.3 - 0.15))
                glow = max(0.0, 1 - math.hypot(dx * 1.05, (dy + 0.12) * 1.1) / 0.75) ** 1.2
                c = _mix(c, core, glow * 0.95)
                bounce = _smooth((d - 0.6) / 0.4) * max(0.0, 0.55 * dx + 0.8 * dy)
                c = [min(255, c[k] + (255, 150, 60)[k] * bounce * 0.55) for k in range(3)]
                c = [v2 * (0.7 + 0.3 * _smooth((1 - d) * 4)) for v2 in c]
                # the cut glint: a small bright quad at the top left, a soft one under it
                glint = math.exp(-(((dx + 0.34) / 0.2) ** 2 + ((dy + 0.46) / 0.13) ** 2) ** 2)
                c = _mix(c, hot, min(1.0, glint * 1.2))
                soft = math.exp(-(((dx + 0.15) / 0.35) ** 2 + ((dy + 0.1) / 0.3) ** 2))
                c = [min(255, c[k] + 40 * soft) for k in range(3)]
                px[x, y] = (*(int(v2) for v2 in c), int(255 * max(0.0, min(1.0, edge_px + 0.5))))
            elif d <= 1.0 + bezel:
                t = (d - 1.0) / bezel
                lit = max(0.0, -0.45 * dx - 0.85 * dy) / max(d, 1e-6)
                c = _mix((16, 10, 6), (150, 104, 56), lit * math.sin(math.pi * t) ** 0.7)
                outer = (1.0 + bezel - d) * gr
                px[x, y] = (*(int(v2) for v2 in c), int(255 * max(0.0, min(1.0, outer + 0.5))))
            else:
                a = math.exp(-(d - 1.0 - bezel) * 2.2) * 0.32
                px[x, y] = (255, 168, 70, int(255 * a))
    return Image.alpha_composite(img, layer)


def _stylebox(path: Path, res: str, margin, content, expand=0, stretch=2, scale=1) -> None:
    """A StyleBoxTexture; margins are given in layout pixels and written in texels."""
    m = margin if isinstance(margin, (tuple, list)) else (margin,) * 4
    c = content if isinstance(content, (tuple, list)) else (content,) * 4
    lines = ['[gd_resource type="StyleBoxTexture" load_steps=2 format=3]', "",
             f'[ext_resource type="Texture2D" path="{res}" id="1"]', "", "[resource]", 'texture = ExtResource("1")']
    lines += [f"texture_margin_{s} = {float(v * scale)}" for s, v in zip(("left", "top", "right", "bottom"), m)]
    lines += [f"content_margin_{s} = {float(v * scale)}" for s, v in zip(("left", "top", "right", "bottom"), c)]
    if expand:
        lines += [f"expand_margin_{s} = {float(expand * scale)}" for s in ("left", "top", "right", "bottom")]
    lines += [f"axis_stretch_horizontal = {stretch}", f"axis_stretch_vertical = {stretch}", ""]
    path.write_text("\n".join(lines))


_MIPMAP_IMPORT = """[remap]

importer="texture"
type="CompressedTexture2D"

[params]

compress/mode=0
mipmaps/generate=true
"""


def _mipmapped(png: Path) -> None:
    """HD art is drawn smaller than its texels, so it gets mipmaps (kept on reimport)."""
    imp = png.with_name(png.name + ".import")
    if not imp.exists():
        imp.write_text(_MIPMAP_IMPORT)
        return
    text = imp.read_text()
    if "mipmaps/generate=false" in text:
        imp.write_text(text.replace("mipmaps/generate=false", "mipmaps/generate=true"))
    elif "mipmaps/generate=" not in text:
        imp.write_text(text.rstrip("\n") + "\nmipmaps/generate=true\n")


class _Console:
    def __init__(self, spec: dict, dest: Path, res_base: str):
        self.spec = _merge(CONSOLE, spec or {})
        self.m = {k: hex_to_rgb(v) for k, v in self.spec["material"].items()}
        self.dest = dest
        self.res = res_base.rstrip("/") + "/"
        self.seed = int(self.spec["seed"])
        self.scale = max(1, int(self.spec["scale"]))
        self.written: list[Path] = []
        self.layout: dict = {}

    def save(self, name: str, img: Image.Image, width: int, height: int) -> None:
        path = self.dest / name
        _down(img, width * self.scale, height * self.scale).save(path)
        if self.scale > 1:
            _mipmapped(path)
        self.written.append(path)

    def stylebox(self, name: str, margin, content, expand=0, stretch=2) -> None:
        _stylebox(self.dest / f"{name}.tres", self.res + f"{name}.png", margin, content, expand=expand,
                  stretch=stretch, scale=self.scale)
        self.written.append(self.dest / f"{name}.tres")

    def relief(self, width, height, mask, seed_offset, period_x=None):
        return _Relief(width, height, mask, self.seed + seed_offset, self.m, period_x, band=self.spec["rim"],
                       grooves=self.spec["grooves"])

    # pieces

    def wing(self, right: bool):
        sp, rail, shadow = self.spec["wing"], self.spec["rail"], self.spec["shadow"]
        width, top_h = sp["size"]
        height = top_h + shadow
        s0, s1 = sp["slope"]

        def top(x):
            x = width - x if right else x
            return top_h if x <= s0 else (rail if x >= s1 else _ogee(x, s0, s1, top_h, rail))

        piece = self.relief(width, height, _profile_mask(width, height, top), 2 if right else 1)
        wx, wb, ww, wh = sp["well"]
        if right:
            wx = width - wx - ww
        wy = height - wb - wh
        so = self.spec["socket"]
        piece.add_socket(wx, wy, ww, wh, wall=so["wall"], bead=so["bead"], depth=so["depth"])
        self.save("wing_right.png" if right else "wing_left.png", piece.paint(), width, height)
        self.layout["card_well" if right else "map_well"] = [wx, wy, ww, wh]
        self.layout["wing"] = [width, height]

    def crest(self):
        sp, rail, shadow = self.spec["centre"], self.spec["rail"], self.spec["shadow"]
        cr = sp["crest"]
        width, height = sp["width"], sp["height"] + shadow
        cx, radius, base = cr["x"], cr["radius"], cr["base"]
        r0, r1 = cr["rise"]
        x0, x1, shoulder = sp["shoulder"]
        start = min(x0, cx)  # the shoulder runs in under the arch, so the two always meet
        springing = base + 2
        f = float(self.spec["fillet"])
        # The left fillet needs the rise to have levelled out where the fillet meets it.
        tangent = cx - math.sqrt(max(0.0, (radius + f) ** 2 - (springing + f - base) ** 2))
        fillets = (_fillet(cx, base, radius, springing, f, -1) if tangent >= r1 else None,
                   _fillet(cx, base, radius, shoulder, f, 1))

        def top(x):
            hv = rail
            if x < cx:
                hv = max(hv, _ogee(x, r0, r1, rail, springing))
            dx = x - cx
            if abs(dx) <= radius:
                hv = max(hv, base + math.sqrt(radius ** 2 - dx * dx))
            if start <= x <= x1 + 30:
                hv = max(hv, shoulder if x <= x1 else _ogee(x, x1, x1 + 30, shoulder, rail))
            for arc in fillets:
                if arc:
                    hv = max(hv, arc(x))
            if abs(dx) < 15:
                hv += 17 * (1 - abs(dx) / 15) ** 1.7
            return hv

        leaf_specs = []  # (stem x, stem y, length, width, angle)
        ccx, ccy = cx, height - base
        for side in (-1, 1):
            for start_deg, length, lw, lift in ((7, 37, 16, 0.20), (31, 25, 11, 0.30)):
                ang = math.radians(-90 + side * start_deg)
                radial = (math.cos(ang), math.sin(ang))
                tangent = (-math.sin(ang) * side, math.cos(ang) * side)
                d = (tangent[0] * math.cos(lift) + radial[0] * math.sin(lift),
                     tangent[1] * math.cos(lift) + radial[1] * math.sin(lift))
                r = radius - 1
                leaf_specs.append((ccx + radial[0] * r, ccy + radial[1] * r, length, lw, math.atan2(d[1], d[0])))

        def leaves(draw, k):
            # Two pairs of oak leaves growing from the gem along the arch, half of each above
            # its edge so they break the silhouette.
            for lx, ly, length, lw, ang in leaf_specs:
                _leaf(draw, k, lx, ly, length, lw, ang)

        def ribs(draw, k):
            for lx, ly, length, lw, ang in leaf_specs:
                _leaf_rib(draw, k, lx, ly, length, 1.1, ang)

        mask = _profile_mask(width, height, top)
        if cr.get("leaves", True):
            mask = ImageChops.lighter(mask, _shape_mask(width, height, leaves))
        piece = self.relief(width, height, mask, 3)
        if sp.get("incised"):
            piece.grooves = list(piece.grooves) + [self.spec["rim"] + sp["incised"]]
        wx, wb, ww, wh = sp["window"]
        window = _shape_mask(width, height, lambda d, k: _arch(d, k, wx, wb, ww, wh, height))
        piece.add_well(window, depth=5.0, window=True)
        apex = height - (base + radius)
        if sp["moulding"]:
            piece.add_frame(window, sp["moulding"], height=3.2)
        piece.add_ornament(_shape_mask(width, height, lambda d, k: d.ellipse(
            [(cx - 10) * k, (apex - 14) * k, (cx + 10) * k, (apex + 10) * k], fill=255)), height=2.4)
        if cr.get("leaves", True):
            piece.add_ornament(_shape_mask(width, height, leaves), height=2.6, soften=1)
            piece.add_ornament(_shape_mask(width, height, ribs), height=-1.4, soften=1, bronze=False)
        piece.add_gem(cx, apex - 2, 6.2)
        self.save("crest.png", piece.paint(), width, height)
        tx, tb, tw, th = sp["text"]
        wy = height - wb - wh
        pad = 3
        self.layout.update({"centre": [width, height], "window": [wx, wy, ww, wh],
                            "backdrop": [wx - pad, wy - pad, ww + 2 * pad, wh + 2 * pad],
                            "text": [tx, height - tb - th, tw, th]})
        self.portrait_bg(ww, wh, pad)

    def rail(self):
        rail, shadow = self.spec["rail"], self.spec["shadow"]
        width, height = 64, rail + shadow
        piece = self.relief(width, height, _profile_mask(width, height, lambda x: rail), 4, period_x=64)
        self.save("rail.png", piece.paint(), width, height)
        self.layout["rail"] = height

    def strip(self):
        """The resource strip hanging from the top edge: a curled cap on the left, then a bar
        whose middle repeats, so a nine-slice can grow it leftwards for big numbers."""
        sp = self.spec["strip"]
        bar, cap, period = sp["height"], sp["cap"], sp["period"]
        width, height = cap + 2 * period, bar + 12
        k = 4 * _S
        img = Image.new("L", (width * k, height * k), 0)
        pts, x = [], -40.0
        while x <= width + 40:
            pts.append((x * k, (bar if x >= cap - 14 else _ogee(x, 4, cap - 14, -2, bar)) * k))
            x += 0.25
        pts += [((width + 40) * k, -40 * k), (-40 * k, -40 * k)]
        ImageDraw.Draw(img).polygon(pts, fill=255)
        piece = self.relief(width, height, img.resize((width * _S, height * _S), Image.BOX), 5, period_x=period)
        self.save("strip.png", piece.paint(), width, height)
        self.stylebox("strip", (cap, 0, 0, 0), (cap, 0, 14, 12), stretch=1)
        self.layout["strip"] = {"height": height, "bar": bar, "cap": cap}

    def plate(self):
        width, height = self.spec["plate"]
        cx = width / 2
        leaf_specs = []
        for side in (-1, 1):
            leaf_specs.append((cx + side * 12, 20, 38, 14, math.pi / 2 - side * (math.pi / 2 - 0.12)))
            leaf_specs.append((cx + side * 42, 22, 24, 10, math.pi / 2 - side * (math.pi / 2 - 0.2)))

        def leaves(draw, k):
            for lx, ly, length, lw, ang in leaf_specs:
                _leaf(draw, k, lx, ly, length, lw, ang)

        def ribs(draw, k):
            for lx, ly, length, lw, ang in leaf_specs:
                _leaf_rib(draw, k, lx, ly, length, 1.1, ang)

        def body(draw, k):
            _rrect(draw, k, 14, 22, width - 28, height - 36, 10)
            draw.ellipse([(cx - 22) * k, 6 * k, (cx + 22) * k, 46 * k], fill=255)
            leaves(draw, k)

        piece = self.relief(width, height, _shape_mask(width, height, body), 6)
        piece.add_ornament(_shape_mask(width, height, lambda d, k: d.ellipse(
            [(cx - 10) * k, 10 * k, (cx + 10) * k, 34 * k], fill=255)), height=2.6)
        piece.add_ornament(_shape_mask(width, height, leaves), height=2.6, soften=1)
        piece.add_ornament(_shape_mask(width, height, ribs), height=-1.4, soften=1, bronze=False)
        piece.add_gem(cx, 21, 6.5)
        self.save("plate.png", piece.paint(), width, height)
        self.layout["plate"] = {"size": [width, height], "text": [30, 54, width - 60, height - 80]}

    def portrait_bg(self, ww, wh, pad):
        """Behind the portrait model: a dark warm vignette with a soft key light behind the
        model's upper half, so a silhouette of any colour reads against it. Shaped like the
        window and `pad` larger all round, so it can clip the portrait to the arch."""
        sp = self.spec["portrait"]
        centre, edge, key = hex_to_rgb(sp["centre"]), hex_to_rgb(sp["edge"]), hex_to_rgb(sp["key"])
        bw, bh = ww + 2 * pad, wh + 2 * pad
        w, h = bw * _S, bh * _S
        shape = _field(_shape_mask(bw, bh, lambda d, k: _arch(d, k, 0, 0, bw, bh, bh)))
        img = Image.new("RGBA", (w, h))
        px = img.load()
        tone = _noise(w, h, 12 * _S, self.seed + 20)
        for y in range(h):
            for x in range(w):
                u, v = (x + 0.5) / w, (y + 0.5) / h
                d = math.hypot((u - 0.5) * 1.15, (v - 0.48) * 0.95) / 0.62
                c = _mix(centre, edge, _smooth(d))
                light = math.exp(-(((u - 0.5) / 0.32) ** 2 + ((v - 0.34) / 0.24) ** 2))
                t = 0.95 + 0.1 * tone[y * w + x]
                px[x, y] = (*(int(min(255, (c[k] + key[k] * light) * t)) for k in range(3)),
                            int(255 * shape[y * w + x]))
        self.save("portrait_bg.png", img, bw, bh)

    def well_shade(self):
        """Inner shadow over the minimap: the socket's top and left lip shade its picture."""
        _, _, ww, wh = self.spec["wing"]["well"]
        w, h = ww * _S, wh * _S
        pad = 20 * _S
        mask = Image.new("L", (w + 2 * pad, h + 2 * pad), 255)
        ImageDraw.Draw(mask).rounded_rectangle([pad + 2 * _S, pad + 4 * _S, pad + w + 2 * _S, pad + h + 4 * _S],
                                               radius=3 * _S, fill=0)
        soft = mask.filter(ImageFilter.GaussianBlur(4 * _S)).crop((pad, pad, pad + w, pad + h))
        img = Image.new("RGBA", (w, h), (6, 4, 3, 0))
        img.putalpha(soft.point(lambda v: int(v * 0.8)))
        self.save("well_shade.png", img, ww, wh)

    def banner(self):
        """Behind announcements: a dark band, soft at every edge, and inside its lower part a
        thin gilt rule with a diamond, so a title reads over bright grass."""
        width, height = self.spec["banner"]
        w, h = width * _S, height * _S
        img = Image.new("RGBA", (w, h))
        px = img.load()
        fall = 24 * _S
        rule_y = h - 26 * _S
        gold, light = hex_to_rgb(TRIMS["gold"][1]), hex_to_rgb(TRIMS["gold"][2])
        for y in range(h):
            fy = _smooth(y / fall) * _smooth((h - 1 - y) / fall)
            for x in range(w):
                dx = abs(x + 0.5 - w / 2) / (w / 2)
                fx = max(0.0, 1 - dx ** 2.4) ** 1.5
                a = 0.8 * fx * fy
                col = (10, 7, 4)
                fade = max(0.0, 1 - dx / 0.6) ** 0.8
                if fade > 0 and abs(y - rule_y) < _S:
                    col = tuple(int(gold[c] + (light[c] - gold[c]) * 0.35) for c in range(3))
                    a = a + (0.95 * fade - a) * fade
                elif fade > 0 and 0 <= y - rule_y - _S < _S:
                    a = max(a, 0.85 * fade * fy)
                px[x, y] = (*col, int(255 * min(1.0, a)))
        draw = ImageDraw.Draw(img)
        cx, r = w // 2, 5 * _S
        draw.polygon([(cx, rule_y - r), (cx + r, rule_y), (cx, rule_y + r), (cx - r, rule_y)], fill=(12, 8, 4, 255))
        r2 = r - _S
        draw.polygon([(cx, rule_y - r2), (cx + r2, rule_y), (cx, rule_y + r2), (cx - r2, rule_y)], fill=(*gold, 255))
        draw.polygon([(cx, rule_y - r2), (cx, rule_y), (cx - r2, rule_y)], fill=(*light, 255))
        self.save("banner.png", img, width, height)
        text_bottom = rule_y // _S - 5
        self.layout["banner"] = {"size": [width, height], "text": [0, 14, width, text_bottom - 14]}

    def glow(self):
        """Hover halo for a command: warm light leaking round the icon."""
        size = self.spec["card"]["socket"] + 20
        w = h = size * _S
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        px = img.load()
        inner = 10 * _S
        for y in range(h):
            for x in range(w):
                dx = max(inner - x, 0, x - (w - 1 - inner))
                dy = max(inner - y, 0, y - (h - 1 - inner))
                if dx == 0 and dy == 0:
                    e = min(x - inner, y - inner, w - 1 - inner - x, h - 1 - inner - y) / _S
                    a = 0.55 * math.exp(-e / 2.5)
                else:
                    a = math.exp(-((math.hypot(dx, dy) / _S / 4.2) ** 1.6))
                px[x, y] = (255, 196, 96, int(255 * a))
        self.save("card_glow.png", img, size, size)
        self.stylebox("card_glow", 16, 0, expand=10, stretch=0)

    def plate_9(self, name, size, radius, rim, fill, fill_alpha, margin, content, glow=None, inset=False):
        """Small nine-slices (tooltip, buttons): a bronze bead round a dark fill."""
        width, height = size
        mask = _shape_mask(width, height, lambda d, k: _rrect(d, k, 1, 1, width - 2, height - 2, radius))
        piece = self.relief(width, height, mask, 11)
        piece.band = rim + 1.5
        piece.grooves = []
        img = piece.paint()
        w, h = img.size
        inner = _shape_mask(width, height, lambda d, k: _rrect(d, k, rim, rim, width - 2 * rim, height - 2 * rim,
                                                               max(1, radius - rim)))
        tone = _noise(w, h, 10 * _S, self.seed + 12)
        data = []
        for (r, g, b, a), mv, t in zip(_px(img), _px(inner), tone):
            f = mv / 255.0
            if f <= 0:
                data.append((r, g, b, a))
                continue
            fc = [fill[c] * (0.9 + 0.2 * t) for c in range(3)]
            data.append((int(r + (fc[0] - r) * f), int(g + (fc[1] - g) * f), int(b + (fc[2] - b) * f),
                         int(a + (fill_alpha - a) * f)))
        img.putdata(data)
        if inset:  # pressed: the fill sinks, a shadow under the top rim
            sh = Image.new("L", (w, h), 0)
            sh.paste(inner.point(lambda v: 255 - v), (0, 3 * _S))
            sh = Image.composite(sh.filter(ImageFilter.GaussianBlur(3 * _S)), Image.new("L", (w, h), 0), inner)
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            layer.putalpha(sh.point(lambda v: int(v * 0.7)))
            img = Image.alpha_composite(img, layer)
        if glow:
            g = inner.filter(ImageFilter.GaussianBlur(6 * _S))
            layer = Image.new("RGBA", (w, h), (*glow, 0))
            edge = [int(max(0, min(255, (255 - abs(v - 128) * 2) * 0.55))) for v in _px(g)]
            layer.putalpha(Image.frombytes("L", (w, h), bytes(edge)))
            img = Image.alpha_composite(img, layer)
        self.save(f"{name}.png", img, width, height)
        # The fill's noise does not repeat, so the middle stretches rather than tiles.
        self.stylebox(name, margin, content, stretch=0)

    def paint(self) -> list[Path]:
        self.dest.mkdir(parents=True, exist_ok=True)
        self.wing(False)
        self.wing(True)
        self.crest()
        self.rail()
        self.strip()
        self.plate()
        self.well_shade()
        self.banner()
        self.glow()
        self.plate_9("tooltip", self.spec["tooltip"], 5, 3, (14, 11, 9), 238, 12, (12, 9, 12, 10))
        for state, glow, inset in (("normal", None, False), ("hover", (255, 190, 90), False),
                                   ("pressed", None, True)):
            self.plate_9(f"button_{state}", self.spec["button"], 6, 4, (34, 26, 20), 255, 12, (16, 6, 16, 6),
                         glow=glow, inset=inset)
        self.plate_9("button_disabled", self.spec["button"], 6, 4, (22, 19, 17), 255, 12, (16, 6, 16, 6))
        card = self.spec["card"]
        self.layout.update({"socket": [card["socket"], card["socket"]], "gap": card["gap"], "scale": self.scale})
        layout = self.dest / "layout.json"
        layout.write_text(json.dumps(self.layout, indent=2) + "\n")
        self.written.append(layout)
        return self.written


def console(spec: dict | None, dest: Path, res_base: str) -> list[Path]:
    """Paints every console piece into the folder `dest` (see CONSOLE for the spec's keys and
    defaults) and returns the files written: PNGs, StyleBoxTextures and layout.json. With
    "scale": 2 the PNGs carry twice the texels of the layout (and mipmaps), the .tres margins
    are in texels, and layout.json stays in layout pixels with its "scale" alongside."""
    global _S
    scale = max(1, int((spec or {}).get("scale", CONSOLE["scale"])))
    previous, _S = _S, 2 * scale
    try:
        return _Console(spec or {}, dest, res_base).paint()
    finally:
        _S = previous
