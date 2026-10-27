"""Image measurement, validation against a style rule, conforming, and review sheets."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .style import Style, contrast_ratio, hex_to_rgb, luminance, rgb_to_hex

# Pixel-level scans (orphans, silhouette edge) run in Python; cap them so a
# folder of 2048 px tilesets does not take minutes.
MAX_SCAN_PIXELS = 256 * 256


# ── Measuring ──

def _opaque_colors(img: Image.Image) -> list[tuple[int, tuple[int, int, int, int]]]:
    # maxcolors sizes PIL's lookup table; the pixel count is the most an image can have.
    return [(n, c) for n, c in img.getcolors(maxcolors=img.width * img.height) if c[3] > 0]


def _scan_pixels(img: Image.Image) -> dict:
    """Orphan pixels and the mean color of the silhouette edge."""
    w, h = img.size
    px = img.load()
    orphans, edge_sum, edge_n = 0, [0, 0, 0], 0
    for y in range(h):
        for x in range(w):
            if px[x, y][3] == 0:
                continue
            neighbours = [px[nx, ny][3] > 0 for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                          if 0 <= nx < w and 0 <= ny < h]
            if not any(neighbours):
                orphans += 1
            # Edge: touches transparency or the image border.
            if len(neighbours) < 4 or not all(neighbours):
                r, g, b, _ = px[x, y]
                edge_sum[0] += r
                edge_sum[1] += g
                edge_sum[2] += b
                edge_n += 1
    edge = tuple(round(v / edge_n) for v in edge_sum) if edge_n else None
    return {"orphan_pixels": orphans, "edge_color": edge}


def _frame_stats(img: Image.Image, frame: tuple[int, int]) -> dict:
    fw, fh = frame
    cols, rows = img.width // fw, img.height // fh
    alpha = img.getchannel("A")
    empty, worst_jitter = [], 0
    for row in range(rows):
        bottoms = []
        for col in range(cols):
            box = alpha.crop((col * fw, row * fh, (col + 1) * fw, (row + 1) * fh)).getbbox()
            if box is None:
                empty.append(row * cols + col)
            else:
                bottoms.append(box[3])
        # Feet that move up and down between frames of one animation read as jitter.
        if len(bottoms) > 1:
            worst_jitter = max(worst_jitter, max(bottoms) - min(bottoms))
    return {"frames": cols * rows, "empty_frames": empty, "baseline_jitter": worst_jitter}


def read_import_params(path: Path) -> dict | None:
    file = path.with_name(path.name + ".import")
    if not file.exists():
        return None
    params, section = {}, ""
    for line in file.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]")
        elif section == "params" and "=" in line:
            key, value = line.split("=", 1)
            params[key] = value.strip('"')
    return params


def analyze_image(path: Path, frame: tuple[int, int] | None = None) -> dict:
    with Image.open(path) as source:
        img = source.convert("RGBA")
        mode = source.mode
    alpha_hist = img.getchannel("A").histogram()
    colors = _opaque_colors(img)
    metrics = {
        "size": list(img.size),
        "mode": mode,
        "colors": len(colors),
        "opaque_pixels": sum(alpha_hist[1:]),
        "transparent_pixels": alpha_hist[0],
        "semi_transparent_pixels": sum(alpha_hist[1:255]),
    }
    if img.width * img.height <= MAX_SCAN_PIXELS and alpha_hist[0] > 0:
        metrics.update(_scan_pixels(img))
    if frame and img.width % frame[0] == 0 and img.height % frame[1] == 0:
        metrics.update(_frame_stats(img, frame))
    return metrics


# ── Validating ──

def _palette_misses(img: Image.Image, palette: list[tuple[int, int, int]], tolerance: float) -> tuple[int, list[str]]:
    off_pixels, off_colors = 0, []
    for count, color in _opaque_colors(img):
        nearest = min(math.dist(color[:3], p) for p in palette)
        if nearest > tolerance:
            off_pixels += count
            off_colors.append((count, rgb_to_hex(color)))
    off_colors.sort(reverse=True)
    return off_pixels, [c for _, c in off_colors[:8]]


def validate_image(project: Path, rel: str, rule: dict, style: Style) -> dict:
    path = project / rel
    frame = tuple(rule["frame"]) if rule.get("frame") else None
    metrics = analyze_image(path, frame)
    problems: list[str] = []
    notes: list[str] = []
    w, h = metrics["size"]

    if "size" in rule and [w, h] != list(rule["size"]):
        problems.append(f"size {w}x{h}, expected {rule['size'][0]}x{rule['size'][1]}")
    if "max_size" in rule and (w > rule["max_size"][0] or h > rule["max_size"][1]):
        problems.append(f"size {w}x{h} exceeds {rule['max_size'][0]}x{rule['max_size'][1]}")
    if "multiple_of" in rule and (w % rule["multiple_of"][0] or h % rule["multiple_of"][1]):
        problems.append(f"size {w}x{h} is not a multiple of {rule['multiple_of'][0]}x{rule['multiple_of'][1]}")
    if frame:
        if w % frame[0] or h % frame[1]:
            problems.append(f"size {w}x{h} does not divide into {frame[0]}x{frame[1]} frames")
        else:
            if metrics.get("empty_frames"):
                notes.append(f"{len(metrics['empty_frames'])} empty frames: {metrics['empty_frames'][:10]}")
            limit = rule.get("max_baseline_jitter")
            if limit is not None and metrics["baseline_jitter"] > limit:
                problems.append(f"feet move {metrics['baseline_jitter']} px between frames (max {limit})")
    if "max_colors" in rule and metrics["colors"] > rule["max_colors"]:
        problems.append(f"{metrics['colors']} colors, max {rule['max_colors']}")
    if rule.get("binary_alpha") and metrics["semi_transparent_pixels"]:
        problems.append(f"{metrics['semi_transparent_pixels']} semi-transparent pixels (pixel art needs 0 or 255 alpha)")
    if rule.get("palette") is not None:
        palette = style.palette(rule["palette"])
        with Image.open(path) as source:
            off_pixels, off_colors = _palette_misses(source.convert("RGBA"), palette, rule.get("palette_tolerance", 0))
        metrics["off_palette_pixels"] = off_pixels
        if off_pixels:
            problems.append(f"{off_pixels} pixels outside palette '{rule['palette']}' (e.g. {', '.join(off_colors[:4])})")
    if "max_orphan_pixels" in rule and metrics.get("orphan_pixels", 0) > rule["max_orphan_pixels"]:
        problems.append(f"{metrics['orphan_pixels']} stray single pixels (max {rule['max_orphan_pixels']})")
    if metrics.get("edge_color"):
        ratio = contrast_ratio(metrics["edge_color"], hex_to_rgb(style.background))
        metrics["silhouette_contrast"] = round(ratio, 2)
        floor = rule.get("min_silhouette_contrast")
        if floor is not None and ratio < floor:
            problems.append(f"silhouette contrast {ratio:.2f} against background {style.background} (min {floor})")
    if "import" in rule:
        params = read_import_params(path)
        if params is None:
            problems.append("not imported by Godot yet (no .import file)")
        else:
            for key, expected in rule["import"].items():
                actual = params.get(key)
                if str(actual).lower() != str(expected).lower():
                    problems.append(f"import {key}={actual}, expected {expected}")
    return {"path": rel, "kind": "image", "ok": not problems, "problems": problems, "notes": notes,
            "metrics": metrics, "rule": rule}


# ── Conforming ──

def _palette_image(palette: list[tuple[int, int, int]]) -> Image.Image:
    flat = [v for rgb in palette for v in rgb]
    flat += flat[:3] * (256 - len(palette))
    holder = Image.new("P", (1, 1))
    holder.putpalette(flat)
    return holder


def _neighbours(x: int, y: int, w: int, h: int):
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nx < w and 0 <= ny < h:
            yield nx, ny


def conform_image(src: Path, dest: Path, rule: dict, style: Style, *, size: tuple[int, int] | None = None,
                  anchor: str = "center", resample: str = "box", trim: bool = True,
                  outline: str | None = None, clean: bool = False, alpha_threshold: int = 128) -> list[str]:
    """Rewrites any image (drawn, downloaded, generated) to satisfy a style rule. Returns the steps taken."""
    steps = []
    img = Image.open(src).convert("RGBA")
    target = size or (tuple(rule["size"]) if rule.get("size") else None)

    if target and img.size != tuple(target):
        if trim and (box := img.getchannel("A").getbbox()):
            img = img.crop(box)
            steps.append(f"trimmed to {img.width}x{img.height}")
        factor = min(target[0] / img.width, target[1] / img.height)
        new_size = (max(1, round(img.width * factor)), max(1, round(img.height * factor)))
        # Box filtering averages each source block, so a large drawing becomes a
        # clean small sprite; nearest keeps already-pixelated art crisp.
        method = Image.NEAREST if resample == "nearest" or factor >= 1 else Image.BOX
        img = img.resize(new_size, method)
        canvas = Image.new("RGBA", tuple(target), (0, 0, 0, 0))
        x = (target[0] - new_size[0]) // 2
        y = target[1] - new_size[1] if anchor == "bottom" else (target[1] - new_size[1]) // 2
        canvas.paste(img, (x, y))
        img = canvas
        steps.append(f"scaled to {new_size[0]}x{new_size[1]} on {target[0]}x{target[1]} ({anchor})")

    alpha = img.getchannel("A")
    if rule.get("binary_alpha", True):
        alpha = alpha.point(lambda a: 255 if a >= alpha_threshold else 0)
        steps.append(f"alpha thresholded at {alpha_threshold}")

    # Fill transparent pixels with an opaque color so they cannot claim a
    # palette slot during quantization; the alpha is restored afterwards.
    rgb = img.convert("RGB")
    opaque = [c for _, c in sorted(_opaque_colors(img), reverse=True)]
    if opaque:
        filler = Image.new("RGB", img.size, opaque[0][:3])
        rgb = Image.composite(rgb, filler, alpha.point(lambda a: 255 if a else 0))
    palette = style.palette(rule.get("palette")) if rule.get("palette") is not None else None
    if palette:
        rgb = rgb.quantize(palette=_palette_image(palette), dither=Image.Dither.NONE).convert("RGB")
        steps.append(f"mapped to palette '{rule['palette']}' ({len(palette)} colors)")
    elif rule.get("max_colors"):
        rgb = rgb.quantize(colors=int(rule["max_colors"]), method=Image.Quantize.MEDIANCUT,
                           dither=Image.Dither.NONE).convert("RGB")
        steps.append(f"reduced to {rule['max_colors']} colors")
    img = rgb.convert("RGBA")
    img.putalpha(alpha)

    w, h = img.size
    px = img.load()
    if clean:
        stray = [(x, y) for y in range(h) for x in range(w)
                 if px[x, y][3] and not any(px[n][3] for n in _neighbours(x, y, w, h))]
        for point in stray:
            px[point] = (0, 0, 0, 0)
        steps.append(f"removed {len(stray)} stray pixels")
    if outline is not None:
        if outline in ("", "auto"):
            color = min(palette, key=luminance) if palette else (0, 0, 0)
        else:
            color = hex_to_rgb(outline)
        ring = [(x, y) for y in range(h) for x in range(w)
                if not px[x, y][3] and any(px[n][3] for n in _neighbours(x, y, w, h))]
        for point in ring:
            px[point] = (*color, 255)
        steps.append(f"outlined in {rgb_to_hex(color)}")
    # Clean transparent pixels so identical sprites produce identical files.
    for y in range(h):
        for x in range(w):
            if px[x, y][3] == 0:
                px[x, y] = (0, 0, 0, 0)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, optimize=True)
    return steps


# ── Review sheets ──

def review_sheet(items: list[dict], project: Path, out: Path, background: str, tile: int = 96,
                 max_items: int = 64) -> Path:
    """Assets on the game's background color, scaled pixel-crisp, with pass/fail borders."""
    items = items[:max_items]
    columns = min(8, max(1, len(items)))
    rows = math.ceil(len(items) / columns)
    label_h, pad = 22, 6
    sheet = Image.new("RGB", (columns * (tile + pad) + pad, rows * (tile + label_h + pad) + pad), (22, 22, 26))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    bg = hex_to_rgb(background)
    for i, item in enumerate(items):
        x = pad + (i % columns) * (tile + pad)
        y = pad + (i // columns) * (tile + label_h + pad)
        border = (90, 200, 120) if item.get("ok", True) else (230, 70, 80)
        draw.rectangle((x - 2, y - 2, x + tile + 1, y + tile + 1), outline=border, width=2)
        draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=bg)
        with Image.open(project / item["path"]) as source:
            img = source.convert("RGBA")
        factor = tile / max(img.size)
        if factor >= 1:
            factor = int(factor)
            img = img.resize((img.width * factor, img.height * factor), Image.NEAREST)
        else:
            img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS)
        sheet.paste(img, (x + (tile - img.width) // 2, y + (tile - img.height) // 2), img)
        name = Path(item["path"]).name
        draw.text((x, y + tile + 3), name if len(name) <= 16 else name[:15] + "…", fill=(225, 225, 230), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
