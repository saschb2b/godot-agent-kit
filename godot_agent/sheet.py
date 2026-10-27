"""Contact sheets: many captures in one image, so a vision model sees a sequence in one look."""

from __future__ import annotations

import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # Pillow is optional; sheets are skipped without it.
    Image = None


def contact_sheet(images: list[tuple[Path, str]], out: Path, max_width: int = 1920,
                  columns: int | None = None) -> Path | None:
    if Image is None or not images:
        return None
    frames = [Image.open(path).convert("RGB") for path, _ in images]
    width, height = frames[0].size
    columns = columns or min(4, len(frames))
    rows = math.ceil(len(frames) / columns)
    # Pixel art stays crisp: whole-number nearest-neighbour upscaling for small
    # viewports, smooth downscaling only when the sheet would be too wide.
    gap, label_h = 4, 16
    usable = max_width - gap * (columns + 1)
    scale = usable / (columns * width)
    scale = max(1, min(int(scale), max(1, 640 // width))) if scale >= 1 else scale
    tile_w, tile_h = max(1, int(width * scale)), max(1, int(height * scale))
    resample = Image.NEAREST if scale >= 1 else Image.LANCZOS

    sheet = Image.new("RGB", (columns * tile_w + gap * (columns + 1), rows * (tile_h + label_h) + gap * (rows + 1)),
                      (22, 22, 26))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (frame, (_, label)) in enumerate(zip(frames, images)):
        col, row = i % columns, i // columns
        x = gap + col * (tile_w + gap)
        y = gap + row * (tile_h + label_h + gap)
        draw.text((x + 2, y + 2), f"{i}: {label}", fill=(230, 230, 235), font=font)
        sheet.paste(frame.resize((tile_w, tile_h), resample), (x, y + label_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def same_pixels(a: Path, b: Path) -> bool:
    if Image is None:
        return a.read_bytes() == b.read_bytes()
    with Image.open(a) as ia, Image.open(b) as ib:
        return ia.size == ib.size and ia.convert("RGBA").tobytes() == ib.convert("RGBA").tobytes()
