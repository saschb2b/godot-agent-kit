"""Low-poly 3D models from seeded recipes, written as glTF binary (.glb), and measured back.

A recipe is a tree of parts (box, cylinder, cone, sphere, rock, wedge) with sizes,
transforms and named colors:

    {"colors": {"bark": "#5a3b28", "leaf": "#c8662a"},
     "parts": [
       {"shape": "cylinder", "radius": 0.07, "radius_top": 0.05, "height": 0.5, "color": "bark"},
       {"shape": "sphere", "radius": [0.3, 0.38], "at": [0, 0.65, 0], "jitter": 0.04, "color": "leaf"},
       {"repeat": 5, "around": 0.2, "parts": [{"shape": "cone", "radius": 0.03, "height": 0.2, "color": "leaf"}]}
     ]}

Any scalar can be a [min, max] range that a seeded RNG resolves, so a (recipe, seed)
pair always gives the same model and `asset regen` can rebuild it. Faces are flat
shaded; each face gets a small brightness variation and faces near the ground are
darkened, which reads as hand-painted low poly at a strategy-game camera distance.
Vertex colors are written in linear space as glTF requires, so an authored hex color
shows as that hex color under neutral light.
"""

from __future__ import annotations

import copy
import json
import math
import random
import struct
from pathlib import Path

from .style import hex_to_rgb, rgb_to_hex
SHAPES = ("box", "cylinder", "cone", "sphere", "rock", "wedge")
PAINTED_MATERIAL = "painted"


# ── Recipes ──
#
# Presets cover the props most 3D games need as set dressing. Their colors are
# names, so a project maps them onto its own palette through the style rule's
# "colors" (see make_mesh in pipeline.py) instead of editing the recipe.

PRESETS: dict[str, dict] = {
    # Trees in the strategy-game manner: tall, layered and lit from above, dark at
    # the base of each mass and bright at the tips, so a forest reads at a glance.
    "tree_round": {
        "colors": {"bark": "#5b3a29", "leaf": "#3f7a2e", "leaf_light": "#7fb444", "leaf_tip": "#c3e070"},
        "parts": [
            {"shape": "cylinder", "radius": [0.08, 0.1], "radius_top": 0.055, "height": [0.7, 0.85], "sides": 7,
             "color": "bark", "jitter": 0.012, "pattern": "bark"},
            {"repeat": 2, "around": 0.05, "parts": [
                {"shape": "cylinder", "radius": 0.035, "radius_top": 0.02, "height": 0.32, "sides": 5,
                 "rotate": [[35, 50], 0, 0], "at": [0, [0.45, 0.6], 0], "color": "bark", "pattern": "bark"}]},
            {"shape": "sphere", "radius": [0.44, 0.52], "segments": [9, 6], "at": [0, [0.98, 1.08], 0],
             "scale": [1, [0.78, 0.9], 1], "jitter": 0.05, "color": "leaf", "color_top": "leaf_light",
             "gradient": 1.3, "pattern": "leaves"},
            {"repeat": [3, 4], "around": [0.26, 0.34], "parts": [
                {"shape": "sphere", "radius": [0.24, 0.31], "segments": [7, 5], "jitter": 0.035,
                 "at": [0, [0.88, 1.18], 0], "color": "leaf", "color_top": "leaf_light", "gradient": 1.2,
                 "pattern": "leaves"}]},
            {"shape": "sphere", "radius": [0.24, 0.3], "segments": [7, 5], "at": [0, [1.32, 1.42], 0], "jitter": 0.03,
             "color": "leaf_light", "color_top": "leaf_tip", "gradient": 1.4, "pattern": "leaves"},
        ],
    },
    "tree_pine": {
        "colors": {"bark": "#4e3427", "needle": "#1d4528", "needle_light": "#3f8a3a", "needle_tip": "#a8d24c"},
        "parts": [
            {"shape": "cylinder", "radius": 0.075, "radius_top": 0.04, "height": 0.6, "sides": 6, "color": "bark",
             "pattern": "bark"},
            {"shape": "cone", "radius": [0.595, 0.672], "height": 0.5, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 0.3, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_light", "gradient": 0.8, "pattern": "thatch"},
            {"shape": "cone", "radius": [0.512, 0.578], "height": 0.48, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 0.58, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_light", "gradient": 0.8, "pattern": "thatch"},
            {"shape": "cone", "radius": [0.428, 0.483], "height": 0.45, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 0.85, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_light", "gradient": 0.8, "pattern": "thatch"},
            {"shape": "cone", "radius": [0.344, 0.389], "height": 0.42, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 1.1, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_light", "gradient": 0.8, "pattern": "thatch"},
            {"shape": "cone", "radius": [0.251, 0.284], "height": 0.4, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 1.34, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_tip", "gradient": 0.8, "pattern": "thatch"},
            {"shape": "cone", "radius": [0.158, 0.179], "height": 0.36, "sides": 14,
             "serrate": [0.3, 0.4], "droop": [0.07, 0.11], "at": [0, 1.56, 0], "rotate": [0, [0, 60], 0],
             "jitter": 0.015, "color": "needle", "color_top": "needle_tip", "gradient": 0.8, "pattern": "thatch"},
        ],
    },
    "bush": {
        "origin": "bottom-center",
        "colors": {"leaf": "#4a7d36", "leaf_light": "#6d9c41"},
        "parts": [
            {"repeat": [3, 4], "around": [0.08, 0.14], "parts": [
                {"shape": "sphere", "radius": [0.12, 0.17], "segments": [6, 4], "at": [0, 0.1, 0],
                 "jitter": 0.025, "color": ["leaf", "leaf_light"], "pattern": "leaves"}]},
            {"shape": "sphere", "radius": [0.13, 0.16], "segments": [6, 4], "at": [0, 0.2, 0], "jitter": 0.02,
             "color": "leaf_light", "pattern": "leaves"},
        ],
    },
    "rock": {
        "origin": "bottom-center",
        "colors": {"stone": "#8a8580", "stone_dark": "#6c6863"},
        "parts": [
            {"shape": "rock", "radius": [0.2, 0.28], "scale": [1, [0.55, 0.75], [0.8, 1.1]], "jitter": 0.06,
             "at": [0, 0.08, 0], "rotate": [0, [0, 360], 0], "color": "stone"},
            {"chance": 0.6, "shape": "rock", "radius": [0.09, 0.13], "scale": [1, 0.7, 1], "jitter": 0.03,
             "at": [[0.18, 0.24], 0.04, [-0.1, 0.1]], "color": "stone_dark"},
        ],
    },
    "grass_tuft": {
        "colors": {"blade": "#6a9a3c", "blade_light": "#8fb84e"},
        "parts": [
            {"repeat": [5, 7], "around": [0.03, 0.07], "parts": [
                {"shape": "cone", "radius": 0.018, "height": [0.12, 0.2], "sides": 3,
                 "rotate": [[10, 25], 0, [-8, 8]], "color": ["blade", "blade_light"]}]},
        ],
        "ground_ao": 0.35,
    },
    "flower": {
        "colors": {"stem": "#4f7f35", "petal": "#e8c04a", "center": "#7a4a20"},
        "parts": [
            {"shape": "cylinder", "radius": 0.008, "height": [0.14, 0.2], "sides": 3, "color": "stem"},
            {"repeat": 5, "around": 0.03, "at": [0, [0.14, 0.2], 0], "parts": [
                {"shape": "sphere", "radius": 0.022, "segments": [4, 3], "scale": [1, 0.4, 1.4], "color": "petal"}]},
            {"shape": "sphere", "radius": 0.016, "segments": [4, 3], "at": [0, [0.15, 0.21], 0], "color": "center"},
        ],
    },
    "mushroom": {
        "colors": {"stalk": "#e6dcc3", "cap": "#b8432f", "spot": "#f2ead8"},
        "parts": [
            {"shape": "cylinder", "radius": 0.025, "radius_top": 0.02, "height": [0.07, 0.1], "sides": 6,
             "color": "stalk"},
            {"shape": "sphere", "radius": [0.055, 0.07], "segments": [7, 4], "scale": [1, 0.6, 1],
             "at": [0, [0.08, 0.1], 0], "color": "cap"},
        ],
    },
    "stump": {
        "colors": {"bark": "#5b3a29", "wood": "#b08a5a"},
        "parts": [
            {"shape": "cylinder", "radius": [0.1, 0.13], "radius_top": [0.08, 0.1], "height": [0.1, 0.16],
             "sides": 7, "jitter": 0.012, "color": "bark", "cap_color": "wood", "pattern": "bark",
             "cap_pattern": "plain"},
            {"repeat": 3, "around": 0.1, "parts": [
                {"shape": "cone", "radius": 0.035, "height": 0.08, "sides": 4, "rotate": [70, 0, 0],
                 "at": [0, 0.01, 0], "color": "bark"}]},
        ],
    },
    "pumpkin": {
        "colors": {"skin": "#d9772b", "stem": "#5a6b2e"},
        "parts": [
            {"shape": "sphere", "radius": [0.11, 0.15], "segments": [12, 6], "lobes": 6, "lobe_depth": 0.14,
             "scale": [1, [0.7, 0.85], 1], "at": [0, 0.1, 0], "color": "skin"},
            {"shape": "cylinder", "radius": 0.016, "radius_top": 0.012, "height": 0.06, "sides": 5,
             "at": [0, 0.19, 0], "rotate": [[-15, 15], 0, [-15, 15]], "color": "stem"},
        ],
    },
    "reeds": {
        "origin": "bottom-center",
        "colors": {"reed": "#6f8f3a", "reed_light": "#94b04c", "cattail": "#6b4528"},
        "parts": [
            {"repeat": [6, 9], "around": [0.02, 0.07], "parts": [
                {"shape": "cone", "radius": 0.012, "height": [0.22, 0.36], "sides": 3,
                 "rotate": [[4, 16], 0, [-6, 6]], "color": ["reed", "reed_light"], "pattern": "leaves"}]},
            {"repeat": [2, 3], "around": [0.01, 0.04], "parts": [
                {"rotate": [[2, 9], 0, [-5, 5]], "parts": [
                    {"shape": "cylinder", "radius": 0.006, "height": 0.4, "sides": 3, "color": "reed"},
                    {"shape": "cylinder", "radius": 0.016, "height": 0.07, "sides": 6, "at": [0, 0.3, 0],
                     "color": "cattail", "pattern": "cloth"}]}]},
        ],
        "ground_ao": 0.15,
    },
    "lily_pad": {
        "origin": "bottom-center",
        "colors": {"pad": "#4f8a3a", "pad_light": "#6aa64a", "petal": "#f2c2d0", "center": "#f0d060"},
        "parts": [
            {"shape": "cylinder", "radius": [0.07, 0.1], "height": 0.008, "sides": 9, "color": ["pad", "pad_light"],
             "pattern": "leaves", "cap_pattern": "leaves"},
            {"chance": 0.5, "parts": [
                {"repeat": 6, "around": 0.018, "at": [0, 0.008, 0], "parts": [
                    {"shape": "sphere", "radius": 0.016, "segments": [4, 3], "scale": [0.7, 0.6, 1.4],
                     "rotate": [-30, 0, 0], "color": "petal"}]},
                {"shape": "sphere", "radius": 0.01, "segments": [4, 3], "at": [0, 0.016, 0], "color": "center"}]},
        ],
        "ground_ao": 0.0,
    },
    "crystal": {
        "colors": {"crystal": "#7fd0e6", "crystal_dark": "#4a8fb8"},
        "parts": [
            {"shape": "cylinder", "radius": 0.06, "radius_top": 0.0, "height": [0.3, 0.42], "sides": 5,
             "color": "crystal", "emit": 0.6},
            {"repeat": [2, 3], "around": 0.07, "parts": [
                {"shape": "cylinder", "radius": 0.04, "radius_top": 0.0, "height": [0.16, 0.24], "sides": 5,
                 "rotate": [[18, 32], 0, 0], "color": "crystal_dark", "emit": 0.4}]},
        ],
    },
}


def preset_recipe(name: str) -> dict:
    if name not in PRESETS:
        raise ValueError(f"unknown mesh preset '{name}'; choose from {', '.join(PRESETS)} or pass a recipe .json")
    return copy.deepcopy(PRESETS[name])


def load_recipe(source: str) -> tuple[str, dict]:
    """A preset name or a recipe .json file -> (label, recipe)."""
    path = Path(source).expanduser()
    if path.suffix == ".json" or path.exists():
        try:
            return path.stem, json.loads(path.read_text())
        except ValueError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from None
    return source, preset_recipe(source)


# ── Resolving ranges ──

def _num(rng: random.Random, value, integer: bool = False):
    """A number, or a [min, max] range drawn from the RNG."""
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        if integer:
            return rng.randint(int(value[0]), int(value[1]))
        return rng.uniform(float(value[0]), float(value[1]))
    if isinstance(value, (int, float)):
        return int(value) if integer else float(value)
    raise ValueError(f"expected a number or [min, max], got {value!r}")


def _vec(rng: random.Random, value, default: float, name: str) -> tuple[float, float, float]:
    """[x, y, z] with any component a range; a scalar or a 2-item range means all three alike."""
    if value is None:
        return (default, default, default)
    if isinstance(value, (int, float)) or (isinstance(value, list) and len(value) == 2):
        v = _num(rng, value)
        return (v, v, v)
    if isinstance(value, list) and len(value) == 3:
        return tuple(_num(rng, c) for c in value)  # type: ignore[return-value]
    raise ValueError(f"'{name}' must be a number, [min, max] or [x, y, z], got {value!r}")


# ── Matrices (3x4, row-major) ──

def _matrix(at=(0.0, 0.0, 0.0), rotate=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)) -> list[list[float]]:
    """Scale, then roll (Z), pitch (X), yaw (Y), then translate. Angles in degrees."""
    rx, ry, rz = (math.radians(a) for a in rotate)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    mx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    my = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    r = _mul3(my, _mul3(mx, mz))
    return [[r[i][0] * scale[0], r[i][1] * scale[1], r[i][2] * scale[2], at[i]] for i in range(3)]


def _mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _compose(parent: list[list[float]], child: list[list[float]]) -> list[list[float]]:
    out = []
    for i in range(3):
        row = [sum(parent[i][k] * child[k][j] for k in range(3)) for j in range(3)]
        row.append(sum(parent[i][k] * child[k][3] for k in range(3)) + parent[i][3])
        out.append(row)
    return out


def _apply(m: list[list[float]], p) -> tuple[float, float, float]:
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))  # type: ignore


IDENTITY = _matrix()


# ── Shapes ──
#
# A shape maker returns (vertices, faces, caps, uv_modes): local-space vertices,
# polygons as vertex-index tuples, the face indices that are caps (cap_color),
# and per-face UV projections ("planar", "cyl", "sphere"). UVs are in metres of
# the scaled part, so painted patterns keep one size across parts and models.

def _ring(n: int, radius: float, y: float, phase: float) -> list[tuple[float, float, float]]:
    return [(radius * math.cos(phase + 2 * math.pi * i / n), y, radius * math.sin(phase + 2 * math.pi * i / n))
            for i in range(n)]


def _shape_box(rng, part):
    w, h, d = _vec(rng, part.get("size"), 0.5, "size")
    # "taper" scales the top face (0.6 = narrower top); "taper_z" sets depth separately.
    tx = _num(rng, part.get("taper", 1.0))
    tz = _num(rng, part["taper_z"]) if "taper_z" in part else tx
    bevel = min(_num(rng, part.get("bevel", 0.0)), 0.45 * min(w, h, d))

    def taper(x: float, y: float, z: float):
        f = y / h if h else 0.0
        return (x * (1 + (tx - 1) * f), y, z * (1 + (tz - 1) * f))

    if bevel <= 0:
        verts = [taper(x, y, z) for x, y, z in (
            (-w / 2, 0, -d / 2), (w / 2, 0, -d / 2), (w / 2, 0, d / 2), (-w / 2, 0, d / 2),
            (-w / 2, h, -d / 2), (w / 2, h, -d / 2), (w / 2, h, d / 2), (-w / 2, h, d / 2))]
        faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        return verts, faces, {0, 1}, None
    # Chamfered box: each corner splits into three vertices, one pushed out along
    # each axis; faces are the inset sides, a strip along every edge, and a
    # triangle at every corner. The strips catch light like a worn, carved edge.
    half = (w / 2, h, d / 2)
    index: dict[tuple, int] = {}
    verts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                signs = (sx, sy, sz)
                for axis in range(3):
                    point = []
                    for a in range(3):
                        if a == 1:
                            full, inset = (h, h - bevel) if signs[1] > 0 else (0.0, bevel)
                        else:
                            full, inset = signs[a] * half[a], signs[a] * (half[a] - bevel)
                        point.append(full if a == axis else inset)
                    index[(sx, sy, sz, axis)] = len(verts)
                    verts.append(taper(*point))
    faces, caps = [], set()
    for axis in range(3):
        others = [a for a in range(3) if a != axis]
        for s in (-1, 1):
            corners = []
            for p, q in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                sg = [0, 0, 0]
                sg[axis], sg[others[0]], sg[others[1]] = s, p, q
                corners.append(index[(*sg, axis)])
            if axis == 1:
                caps.add(len(faces))
            faces.append(tuple(corners))
    for a in range(3):
        b1, b2 = [x for x in range(3) if x != a]
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                quad = []
                for sa, owner in ((-1, b1), (1, b1), (1, b2), (-1, b2)):
                    sg = [0, 0, 0]
                    sg[a], sg[b1], sg[b2] = sa, s1, s2
                    quad.append(index[(*sg, owner)])
                faces.append(tuple(quad))
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                faces.append(tuple(index[(sx, sy, sz, a)] for a in range(3)))
    return verts, faces, caps, None


def _shape_cylinder(rng, part, cone: bool = False):
    sides = max(3, _num(rng, part.get("sides", 8), integer=True))
    r0 = _num(rng, part.get("radius", 0.2))
    r1 = 0.0 if cone else _num(rng, part.get("radius_top", r0))
    h = _num(rng, part.get("height", 0.5))
    phase = math.radians(_num(rng, part.get("phase", 0.0)))
    bevel = min(_num(rng, part.get("bevel", 0.0)), 0.4 * h, 0.4 * max(r0, 1e-6))
    # Rings from bottom to top; a bevel adds a chamfer ring at each rim.
    rings = [(r0, 0.0)]
    if bevel > 0:
        rings = [(r0 - bevel, 0.0), (r0, bevel)]
        rings += [(r1, h - bevel), (max(r1 - bevel, 0.0), h)] if r1 > 1e-6 else [(0.0, h)]
    else:
        rings.append((r1, h))
    # "serrate" pulls every other bottom-rim vertex inward and "droop" lowers the
    # rest: a jagged, hanging rim, like a pine bough's tips.
    serrate = _num(rng, part.get("serrate", 0.0))
    droop = _num(rng, part.get("droop", 0.0))
    verts: list = []
    faces: list[tuple[int, ...]] = []
    modes: list[str] = []
    starts = []
    for k, (radius, y) in enumerate(rings):
        starts.append(len(verts))
        if radius <= 1e-6:
            verts.append((0.0, y, 0.0))
        elif k == 0 and (serrate or droop):
            ring = _ring(sides, radius, y, phase)
            verts += [(px * (1 - serrate), py, pz * (1 - serrate)) if i % 2 else (px, py - droop, pz)
                      for i, (px, py, pz) in enumerate(ring)]
        else:
            verts += _ring(sides, radius, y, phase)
    for k in range(len(rings) - 1):
        a, b = starts[k], starts[k + 1]
        a_tip, b_tip = rings[k][0] <= 1e-6, rings[k + 1][0] <= 1e-6
        for i in range(sides):
            j = (i + 1) % sides
            if b_tip:
                faces.append((a + i, a + j, b))
            elif a_tip:
                faces.append((a, b + j, b + i))
            else:
                faces.append((a + i, a + j, b + j, b + i))
            modes.append("cyl")
    caps = set()
    if rings[0][0] > 1e-6:
        caps.add(len(faces))
        faces.append(tuple(range(starts[0], starts[0] + sides)))
        modes.append("planar")
    if rings[-1][0] > 1e-6:
        caps.add(len(faces))
        faces.append(tuple(range(starts[-1], starts[-1] + sides)))
        modes.append("planar")
    return verts, faces, caps, modes


def _shape_sphere(rng, part):
    r = _num(rng, part.get("radius", 0.25))
    segments = part.get("segments", [8, 5])
    lon = max(3, _num(rng, segments[0], integer=True))
    lat = max(2, _num(rng, segments[1], integer=True))
    lobes = int(part.get("lobes", 0))
    depth = _num(rng, part.get("lobe_depth", 0.12))
    verts = [(0.0, -r, 0.0)]
    for j in range(1, lat):
        theta = math.pi * j / lat
        for i in range(lon):
            phi = 2 * math.pi * i / lon
            rr = r * (1 - depth * (0.5 - 0.5 * math.cos(lobes * phi))) if lobes else r
            verts.append((rr * math.sin(theta) * math.cos(phi), -r * math.cos(theta), rr * math.sin(theta) * math.sin(phi)))
    verts.append((0.0, r, 0.0))
    top = len(verts) - 1
    faces: list[tuple[int, ...]] = [(0, 1 + (i + 1) % lon, 1 + i) for i in range(lon)]
    for j in range(lat - 2):
        a, b = 1 + j * lon, 1 + (j + 1) * lon
        faces += [(a + i, a + (i + 1) % lon, b + (i + 1) % lon, b + i) for i in range(lon)]
    last = 1 + (lat - 2) * lon
    faces += [(last + i, last + (i + 1) % lon, top) for i in range(lon)]
    return verts, faces, set(), ["sphere"] * len(faces)


def _shape_rock(rng, part):
    """Icosahedron (detail 0) or its once-subdivided sphere (detail 1), centred."""
    r = _num(rng, part.get("radius", 0.25))
    t = (1 + 5 ** 0.5) / 2
    base = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
            (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    verts = [_normalize(v) for v in base]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
             (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11),
             (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    if int(part.get("detail", 0)) >= 1:
        midpoints: dict[tuple[int, int], int] = {}

        def mid(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in midpoints:
                pa, pb = verts[a], verts[b]
                verts.append(_normalize(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, (pa[2] + pb[2]) / 2)))
                midpoints[key] = len(verts) - 1
            return midpoints[key]

        faces = [f for a, b, c in faces for f in ((a, mid(a, b), mid(c, a)), (b, mid(b, c), mid(a, b)),
                                                  (c, mid(c, a), mid(b, c)), (mid(a, b), mid(b, c), mid(c, a)))]
    return [(x * r, y * r, z * r) for x, y, z in verts], faces, set(), None


def _shape_wedge(rng, part):
    """Triangular prism: a roof or a blade. Triangle in the XY plane, extruded along Z."""
    w, h, d = _vec(rng, part.get("size"), 0.5, "size")
    verts = [(-w / 2, 0, -d / 2), (w / 2, 0, -d / 2), (0, h, -d / 2), (-w / 2, 0, d / 2), (w / 2, 0, d / 2),
             (0, h, d / 2)]
    faces = [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)]
    return verts, faces, set(), None


def _normalize(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / length for c in v)


# ── UVs ──

def _unwrap(angles: list[float]) -> list[float]:
    """Keeps a face's angles on one side of the ±π seam."""
    if max(angles) - min(angles) > math.pi:
        return [a + 2 * math.pi if a < 0 else a for a in angles]
    return angles


def _face_uvs(local: list, mode: str, scale: tuple, info: dict) -> list[tuple[float, float]]:
    sx, sy, sz = scale
    if mode == "cyl":
        angles = _unwrap([math.atan2(p[2], p[0]) for p in local])
        radius = info["radius"] * (sx + sz) / 2
        return [(a * radius, p[1] * sy * info["slant"]) for a, p in zip(angles, local)]
    if mode == "sphere":
        angles = _unwrap([math.atan2(p[2], p[0]) for p in local])
        r = info["radius"]
        out = []
        for a, p in zip(angles, local):
            theta = math.acos(max(-1.0, min(1.0, p[1] / r))) if r else 0.0
            out.append((a * r * (sx + sz) / 2, (math.pi - theta) * r * sy))
        return out
    # Planar: project along the face's dominant axis, keeping y as "up" on walls.
    n = _face_normal(tuple(local[:3])) or (0.0, 1.0, 0.0)
    ax, ay, az = (abs(c) for c in n)
    if ay >= ax and ay >= az:
        return [(p[0] * sx, p[2] * sz) for p in local]
    if ax >= az:
        return [(p[2] * sz, p[1] * sy) for p in local]
    return [(p[0] * sx, p[1] * sy) for p in local]


# Surface patterns the painted shader draws, by index in COLOR_0's alpha.
PATTERNS = ("plain", "bricks", "planks", "shingles", "metal", "cobble", "cloth", "gold", "crystal", "bark",
            "leaves", "thatch", "stone")


def _pattern_alpha(name: str | None) -> float:
    if name in (None, "plain"):
        return (0 + 0.5) / 16
    if name not in PATTERNS:
        raise ValueError(f"unknown pattern {name!r}; use one of {', '.join(PATTERNS)}")
    return (PATTERNS.index(name) + 0.5) / 16


# ── Building ──

class _Builder:
    def __init__(self, recipe: dict, seed: int):
        self.recipe = recipe
        self.rng = random.Random(f"mesh:{seed}")
        self.colors = {k: hex_to_rgb(v) for k, v in recipe.get("colors", {}).items()}
        self.facet = float(recipe.get("facet", 0.06))
        # (positions, uvs, srgb color, emit, pattern alpha) per triangle
        self.tris: list[tuple] = []

    def color(self, ref) -> tuple[int, int, int]:
        if isinstance(ref, list):
            ref = self.rng.choice(ref)
        if ref is None:
            return (200, 200, 200)
        if isinstance(ref, str) and ref.startswith("#"):
            return hex_to_rgb(ref)
        if ref not in self.colors:
            raise ValueError(f"color '{ref}' is not defined in the recipe's colors {sorted(self.colors)}")
        return self.colors[ref]

    def parts(self, parts: list, parent: list[list[float]]) -> None:
        for part in parts:
            if "chance" in part and self.rng.random() >= float(part["chance"]):
                continue
            if "repeat" in part:
                count = _num(self.rng, part["repeat"], integer=True)
                around = part.get("around")
                phase = _num(self.rng, part.get("phase", [0, 360])) if around is not None else 0.0
                for i in range(count):
                    group = self._local(part)
                    if around is not None:
                        angle = phase + 360.0 * i / count
                        radius = _num(self.rng, around)
                        a = math.radians(angle)
                        # Each copy sits on the ring with its local +Z pointing outward,
                        # so a positive pitch (rotate X) leans it away from the centre.
                        ring = _matrix((radius * math.cos(a), 0.0, -radius * math.sin(a)), (0.0, angle + 90.0, 0.0))
                        group = _compose(ring, group)
                    self.parts(part.get("parts", []), _compose(parent, group))
            elif "parts" in part:
                self.parts(part["parts"], _compose(parent, self._local(part)))
            else:
                self.shape(part, _compose(parent, self._local(part)))

    def _local(self, part: dict) -> list[list[float]]:
        return _matrix(_vec(self.rng, part.get("at"), 0.0, "at"), _vec(self.rng, part.get("rotate"), 0.0, "rotate"),
                       _vec(self.rng, part.get("scale"), 1.0, "scale"))

    def shape(self, part: dict, m: list[list[float]]) -> None:
        kind = part.get("shape")
        if kind not in SHAPES:
            raise ValueError(f"unknown shape {kind!r}; use one of {', '.join(SHAPES)}")
        maker = {"box": _shape_box, "cylinder": _shape_cylinder, "sphere": _shape_sphere, "rock": _shape_rock,
                 "wedge": _shape_wedge, "cone": lambda r, p: _shape_cylinder(r, p, cone=True)}[kind]
        local, faces, caps, modes = maker(self.rng, part)
        modes = modes or ["planar"] * len(faces)
        info = self._uv_info(part, local)
        jitter = _num(self.rng, part.get("jitter", 0.0))
        jittered = local
        if jitter:
            jittered = [(x + self.rng.uniform(-jitter, jitter), y + self.rng.uniform(-jitter, jitter),
                         z + self.rng.uniform(-jitter, jitter)) for x, y, z in local]
        verts = [_apply(m, v) for v in jittered]
        center = tuple(sum(v[i] for v in verts) / len(verts) for i in range(3))
        scale = tuple(math.sqrt(m[0][c] ** 2 + m[1][c] ** 2 + m[2][c] ** 2) for c in range(3))
        pattern_scale = float(part.get("pattern_scale", 1.0)) or 1.0
        swap = bool(part.get("pattern_rotate", False))
        base = self.color(part.get("color"))
        cap = self.color(part["cap_color"]) if "cap_color" in part else base
        top = self.color(part["color_top"]) if "color_top" in part else None
        ys = [v[1] for v in local]
        y0, y1 = min(ys), max(ys)
        gamma = float(part.get("gradient", 1.0))

        def shade_of(i: int, flat: tuple) -> tuple:
            if top is None:
                return flat
            f = ((local[i][1] - y0) / (y1 - y0)) ** gamma if y1 > y0 else 0.0
            return tuple(flat[c] + (top[c] - flat[c]) * f for c in range(3))
        pattern = _pattern_alpha(part.get("pattern"))
        cap_pattern = _pattern_alpha(part["cap_pattern"]) if "cap_pattern" in part else pattern
        emit = float(part.get("emit", 0.0))
        for index, face in enumerate(faces):
            is_cap = index in caps
            color = cap if is_cap else base
            uvs = _face_uvs([local[i] for i in face], modes[index], scale, info)
            uvs = [((v, u) if swap else (u, v)) for u, v in uvs]
            uvs = [(u / pattern_scale, v / pattern_scale) for u, v in uvs]
            for k in range(1, len(face) - 1):
                tri = (verts[face[0]], verts[face[k]], verts[face[k + 1]])
                tri_uv = (uvs[0], uvs[k], uvs[k + 1])
                tri_color = tuple(shade_of(face[j], color) if not is_cap or "cap_color" not in part else color
                                  for j in (0, k, k + 1))
                normal = _face_normal(tri)
                if normal is None:
                    continue
                # Primitives are convex or star-shaped around their centre, so a face
                # whose normal points at the centre is wound the wrong way.
                mid = tuple(sum(p[i] for p in tri) / 3 for i in range(3))
                if sum(normal[i] * (mid[i] - center[i]) for i in range(3)) < 0:
                    tri = (tri[0], tri[2], tri[1])
                    tri_uv = (tri_uv[0], tri_uv[2], tri_uv[1])
                    tri_color = (tri_color[0], tri_color[2], tri_color[1])
                self.tris.append((tri, tri_uv, tri_color, emit, cap_pattern if is_cap else pattern))

    @staticmethod
    def _uv_info(part: dict, local: list) -> dict:
        """Radius and slant factor for cylindrical and spherical unwraps."""
        radius = max((math.hypot(p[0], p[2]) for p in local), default=0.2) or 0.2
        height = (max(p[1] for p in local) - min(p[1] for p in local)) or 1.0
        bottom = max((math.hypot(p[0], p[2]) for p in local if p[1] <= min(q[1] for q in local) + 1e-6), default=0.0)
        top = max((math.hypot(p[0], p[2]) for p in local if p[1] >= max(q[1] for q in local) - 1e-6), default=0.0)
        slant = math.hypot(height, bottom - top) / height
        return {"radius": radius * 0.85, "slant": slant}

    def build(self) -> dict:
        self.parts(self.recipe.get("parts", []), IDENTITY)
        if not self.tris:
            raise ValueError("recipe produced no geometry")
        # "bottom" (default) puts the lowest point on y=0; "bottom-center" also centres the
        # footprint, for clusters without a natural pivot; "authored" keeps coordinates.
        origin = self.recipe.get("origin", "bottom")
        points = [p for t in self.tris for p in t[0]]
        shift = [0.0, 0.0, 0.0]
        if origin in ("bottom", "bottom-center"):
            shift[1] = -min(p[1] for p in points)
        if origin == "bottom-center":
            for axis in (0, 2):
                shift[axis] = -(min(p[axis] for p in points) + max(p[axis] for p in points)) / 2
        if any(shift):
            self.tris = [(tuple((p[0] + shift[0], p[1] + shift[1], p[2] + shift[2]) for p in tri), uv, c, e, pat)
                         for tri, uv, c, e, pat in self.tris]
        ao = float(self.recipe.get("ground_ao", 0.25))
        ao_height = float(self.recipe.get("ao_height", 0.3))
        surfaces: dict[float, dict] = {}
        for tri, uv, colors, emit, pattern in self.tris:
            surface = surfaces.setdefault(emit, {"positions": [], "normals": [], "colors": [], "uvs": []})
            normal = _face_normal(tri)
            shade = 1 + self.rng.uniform(-self.facet, self.facet)
            for p, t, color in zip(tri, uv, colors):
                dark = 1 - ao * max(0.0, 1 - p[1] / ao_height) if ao_height > 0 else 1
                rgb = tuple(min(255.0, c * shade * dark) for c in color)
                surface["positions"].append(p)
                surface["normals"].append(normal)
                surface["uvs"].append(t)
                surface["colors"].append((*(_to_linear(c / 255) for c in rgb), pattern))
        return {"surfaces": [{"emit": emit, **data} for emit, data in sorted(surfaces.items())]}


def _face_normal(tri):
    a, b, c = tri
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    return None if length < 1e-12 else (n[0] / length, n[1] / length, n[2] / length)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def build_mesh(recipe: dict, seed: int = 1) -> dict:
    return _Builder(recipe, seed).build()


# ── glTF binary ──

def write_glb(mesh: dict, path: Path, name: str = "model") -> dict:
    """One node, one mesh; a vertex-colored surface plus one per emission strength."""
    buffer = bytearray()
    accessors: list[dict] = []
    views: list[dict] = []
    primitives: list[dict] = []
    materials: list[dict] = []

    def add(data: bytes, accessor: dict, target: int) -> int:
        while len(buffer) % 4:
            buffer.append(0)
        views.append({"buffer": 0, "byteOffset": len(buffer), "byteLength": len(data), "target": target})
        buffer.extend(data)
        accessors.append({"bufferView": len(views) - 1, **accessor})
        return len(accessors) - 1

    tris = 0
    for surface in mesh["surfaces"]:
        positions, normals, colors = surface["positions"], surface["normals"], surface["colors"]
        count = len(positions)
        tris += count // 3
        lo = [min(p[i] for p in positions) for i in range(3)]
        hi = [max(p[i] for p in positions) for i in range(3)]
        pos = add(struct.pack(f"<{count * 3}f", *(c for p in positions for c in p)),
                  {"componentType": 5126, "count": count, "type": "VEC3", "min": lo, "max": hi}, 34962)
        nrm = add(struct.pack(f"<{count * 3}f", *(c for n in normals for c in n)),
                  {"componentType": 5126, "count": count, "type": "VEC3"}, 34962)
        # Alpha carries the painted pattern index (see PATTERNS), not transparency.
        col = add(struct.pack(f"<{count * 4}f", *(c for rgba in colors for c in rgba)),
                  {"componentType": 5126, "count": count, "type": "VEC4"}, 34962)
        uv = add(struct.pack(f"<{count * 2}f", *(c for t in surface["uvs"] for c in t)),
                 {"componentType": 5126, "count": count, "type": "VEC2"}, 34962)
        index_type, fmt = (5123, "H") if count < 65536 else (5125, "I")
        idx = add(struct.pack(f"<{count}{fmt}", *range(count)), {"componentType": index_type, "count": count,
                                                                   "type": "SCALAR"}, 34963)
        primitive = {"attributes": {"POSITION": pos, "NORMAL": nrm, "COLOR_0": col, "TEXCOORD_0": uv},
                     "indices": idx}
        if surface["emit"]:
            # glTF has no vertex-colored emission; the mean color, scaled by the strength, comes close.
            mean = [sum(c[i] for c in colors) / count for i in range(3)]
            materials.append({"name": f"glow_{surface['emit']:g}", "emissiveFactor": [
                round(min(1.0, m * surface["emit"] * 2), 4) for m in mean], "pbrMetallicRoughness": {
                "baseColorFactor": [*mean, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0}})
            primitive["material"] = len(materials) - 1
        else:
            # Godot ignores COLOR_0 for albedo on import, so this material is named and
            # replaced by a vertex-color material through the .import file (see pipeline).
            materials.append({"name": PAINTED_MATERIAL, "pbrMetallicRoughness": {
                "baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0}})
            primitive["material"] = len(materials) - 1
        primitives.append(primitive)
    gltf = {
        "asset": {"version": "2.0", "generator": "godot-agent asset mesh"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}], **({"materials": materials} if materials else {}),
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(buffer)}],
    }
    payload = json.dumps(gltf, separators=(",", ":")).encode()
    payload += b" " * (-len(payload) % 4)
    while len(buffer) % 4:
        buffer.append(0)
    body = struct.pack("<II", len(payload), 0x4E4F534A) + payload + struct.pack("<II", len(buffer), 0x004E4942) + buffer
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return {"tris": tris, "surfaces": len(primitives)}


# ── Reading any .glb/.gltf back ──

_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _read_gltf(path: Path) -> tuple[dict, list[bytes]]:
    data = path.read_bytes()
    if path.suffix.lower() == ".glb":
        magic, _, _ = struct.unpack_from("<III", data, 0)
        if magic != 0x46546C67:
            raise ValueError("not a glTF binary file")
        offset, gltf, binary = 12, None, b""
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            chunk = data[offset + 8: offset + 8 + length]
            if kind == 0x4E4F534A:
                gltf = json.loads(chunk)
            elif kind == 0x004E4942:
                binary = chunk
            offset += 8 + length
        if gltf is None:
            raise ValueError("glb has no JSON chunk")
        buffers = [binary]
    else:
        gltf = json.loads(data)
        buffers = []
        for buf in gltf.get("buffers", []):
            uri = buf.get("uri", "")
            if uri.startswith("data:"):
                import base64
                buffers.append(base64.b64decode(uri.split(",", 1)[1]))
            else:
                buffers.append((path.parent / uri).read_bytes())
    return gltf, buffers


def _accessor(gltf: dict, buffers: list[bytes], index: int) -> list[tuple]:
    acc = gltf["accessors"][index]
    fmt, size = _COMPONENTS[acc["componentType"]]
    width = _WIDTH[acc["type"]]
    if "bufferView" not in acc:
        return [(0,) * width] * acc["count"]
    view = gltf["bufferViews"][acc["bufferView"]]
    data = buffers[view.get("buffer", 0)]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or size * width
    out = []
    for i in range(acc["count"]):
        values = struct.unpack_from(f"<{width}{fmt}", data, start + i * stride)
        if acc.get("normalized") or (fmt in "BH" and acc["type"] != "SCALAR"):
            scale = float((1 << (8 * size)) - 1)
            values = tuple(v / scale for v in values)
        out.append(values)
    return out


def _node_matrix(node: dict) -> list[list[float]]:
    if "matrix" in node:
        m = node["matrix"]  # column-major 4x4
        return [[m[0], m[4], m[8], m[12]], [m[1], m[5], m[9], m[13]], [m[2], m[6], m[10], m[14]]]
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    tx, ty, tz = node.get("translation", [0, 0, 0])
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx], [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz]]


def analyze_model(path: Path) -> dict:
    """Triangles, bounds (with node transforms), materials and vertex colors (sRGB) of a glTF file."""
    gltf, buffers = _read_gltf(path)
    tris, colors, points = 0, set(), []
    # Surfaces without COLOR_0 (emissive eyes, textured parts) are skipped, not fatal.
    has_vertex_colors = False

    def visit(index: int, parent: list[list[float]]) -> None:
        nonlocal tris, has_vertex_colors
        node = gltf["nodes"][index]
        m = _compose(parent, _node_matrix(node))
        if "mesh" in node:
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    continue
                positions = _accessor(gltf, buffers, prim["attributes"]["POSITION"])
                count = gltf["accessors"][prim["indices"]]["count"] if "indices" in prim else len(positions)
                tris += count // 3
                points.extend(_apply(m, p) for p in positions)
                if "COLOR_0" in prim["attributes"]:
                    has_vertex_colors = True
                    for c in _accessor(gltf, buffers, prim["attributes"]["COLOR_0"]):
                        colors.add(tuple(round(_to_srgb(v) * 255) for v in c[:3]))
        for child in node.get("children", []):
            visit(child, m)

    scene = gltf.get("scenes", [{"nodes": list(range(len(gltf.get("nodes", []))))}])[gltf.get("scene", 0)]
    for root in scene.get("nodes", []):
        visit(root, IDENTITY)
    if not points:
        raise ValueError("model has no triangles")
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return {
        "tris": tris,
        "size": [round(hi[i] - lo[i], 3) for i in range(3)],
        "min": [round(v, 3) for v in lo],
        "max": [round(v, 3) for v in hi],
        "center_xz": [round((lo[0] + hi[0]) / 2, 3), round((lo[2] + hi[2]) / 2, 3)],
        "materials": len(gltf.get("materials", [])),
        "material_names": [m.get("name", "") for m in gltf.get("materials", [])],
        "animations": [a.get("name", "") for a in gltf.get("animations", [])],
        "vertex_colors": sorted(colors) if has_vertex_colors else None,
    }


def _paint_matches(color, palette, tolerance: float) -> bool:
    """Same paint under different light: color ≈ k·palette color for a k in [0.5, 1.25]."""
    for p in palette:
        dot = sum(a * b for a, b in zip(color, p))
        norm = sum(b * b for b in p) or 1
        k = min(1.25, max(0.5, dot / norm))
        if math.dist(color, [k * b for b in p]) <= tolerance:
            return True
    return False


def validate_model(project: Path, rel: str, rule: dict, style) -> dict:
    metrics = analyze_model(project / rel)
    problems: list[str] = []
    notes: list[str] = []
    if "max_tris" in rule and metrics["tris"] > rule["max_tris"]:
        problems.append(f"{metrics['tris']} triangles, max {rule['max_tris']}")
    for key, cmp, word in (("max_size", lambda a, b: a > b, "max"), ("min_size", lambda a, b: a < b, "min")):
        if key in rule:
            limit = rule[key]
            for axis, value, bound in zip("xyz", metrics["size"], limit):
                if bound is not None and cmp(value, bound):
                    problems.append(f"size {axis}={value}, {word} {bound}")
    tolerance = float(rule.get("origin_tolerance", 0.02))
    origin = rule.get("origin")
    if origin in ("bottom", "bottom-center") and abs(metrics["min"][1]) > tolerance:
        problems.append(f"lowest point at y={metrics['min'][1]}, expected 0 (origin at the base, so it stands "
                        f"on the ground when placed)")
    if origin in ("center", "bottom-center"):
        span = max(metrics["size"][0], metrics["size"][2]) or 1
        cx, cz = metrics["center_xz"]
        if max(abs(cx), abs(cz)) > max(tolerance, 0.15 * span):
            problems.append(f"footprint centred at x={cx}, z={cz}; expected near 0")
    for name in rule.get("animations", []):
        if name not in metrics["animations"]:
            problems.append(f"no '{name}' animation (has {metrics['animations'] or 'none'})")
    if "max_materials" in rule and metrics["materials"] > rule["max_materials"]:
        problems.append(f"{metrics['materials']} materials, max {rule['max_materials']} (each is a draw call)")
    palette = style.palette(rule.get("palette")) if rule.get("palette") else None
    if palette:
        colors = metrics["vertex_colors"]
        if colors is not None and any(n.startswith("foliage") for n in metrics["material_names"]):
            # Foliage tints multiply a grey card; their colours are not paint.
            notes.append("foliage tints not checked against the palette")
            colors = None
        if colors is None:
            notes.append("no vertex colors; palette not checked")
        else:
            misses = [c for c in colors if not _paint_matches(c, palette, float(rule.get("palette_tolerance", 24)))]
            if misses:
                problems.append(f"{len(misses)} vertex colors outside the palette, e.g. "
                                f"{', '.join(rgb_to_hex(c) for c in misses[:4])}")
    metrics.pop("vertex_colors", None)
    return {"path": rel, "kind": "model", "ok": not problems, "problems": problems, "notes": notes,
            "metrics": metrics, "rule": rule}


# ── Seeing models ──

def _run_model_script(project: Path, listing: dict, frames: Path, timeout: int) -> None:
    """Runs runtime/model_review.gd on the private Xvfb display."""
    import os
    import subprocess

    from ..env import RUNTIME_DIR, GodotAgentError, find_godot, virtual_display

    frames.mkdir(parents=True, exist_ok=True)
    list_file = frames / "models.json"
    list_file.write_text(json.dumps(listing))
    cmd = [find_godot(), "--path", str(project), "--display-driver", "x11", "--audio-driver", "Dummy",
           "--resolution", "64x64", "-s", str(RUNTIME_DIR / "model_review.gd"), "--",
           f"--ga-list={list_file}", f"--ga-out={frames}"]
    with virtual_display() as display:
        if display is None:
            raise GodotAgentError("rendering models needs Xvfb (sudo apt install xvfb)")
        env = dict(os.environ, DISPLAY=display)
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if not (frames / "info.json").exists():
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        raise GodotAgentError(f"model render failed:\n{tail}")


def render_icons(project: Path, models: list[Path], out: Path, size: int = 64, pitch: float = 30.0,
                 yaw: float = 35.0, material: dict | None = None, frame: dict | None = None,
                 timeout: int = 300) -> list[Path]:
    """One 3/4-view PNG per model, rendered at 2x and downscaled for clean edges.

    Transparent by default; `frame` paints it as a framed button icon instead
    (see icon_frame in ui.py).
    """
    from PIL import Image

    frames = out / "frames"
    _run_model_script(project, {"icon": True, "size": size * 2, "pitch": pitch, "yaw": yaw, "materials": material or {},
                                "models": [{"path": str(m), "label": m.stem} for m in models]}, frames, timeout)
    results = []
    for i, model in enumerate(models):
        with Image.open(frames / f"{i}_icon.png") as img:
            # Downscale premultiplied ("RGBa"): edge pixels with little alpha would
            # otherwise bleed whatever color the renderer left in them.
            icon = img.convert("RGBA").convert("RGBa").resize((size, size), Image.BOX).convert("RGBA")
        clean = Image.new("RGBA", icon.size, (0, 0, 0, 0))
        icon = Image.composite(icon, clean, icon.getchannel("A").point(lambda a: 255 if a >= 8 else 0))
        if frame is not None:
            from . import ui
            icon = ui.icon_frame(icon, **frame)
        target = out / f"{model.stem}.png"
        icon.save(target)
        results.append(target)
    return results


def render_review(project: Path, items: list[dict], out: Path, background: str, size: int = 256,
                  views: int = 4, pitch: float = 45.0, material: dict | None = None, timeout: int = 300) -> Path:
    """Turntable views per model plus a true-scale lineup, rendered by Godot, as one sheet.

    items: [{"path": rel or absolute, "ok": bool, "summary": "..."}]. Rendering needs a
    display, so this uses the private Xvfb display the scenario runner uses.
    """
    from PIL import Image, ImageDraw, ImageFont

    frames = out / "frames"
    models = [{"path": str((project / it["path"]) if not Path(it["path"]).is_absolute() else it["path"]),
               "label": Path(it["path"]).stem} for it in items]
    _run_model_script(project, {"models": models, "size": size, "views": views, "pitch": pitch,
                                "background": background, "materials": material or {}}, frames, timeout)

    font = ImageFont.load_default()
    label_h, pad = 18, 6
    row_w = views * (size + pad) + pad
    lineup = Image.open(frames / "lineup.png").convert("RGB") if (frames / "lineup.png").exists() else None
    if lineup and lineup.width > row_w:
        lineup = lineup.resize((row_w - 2 * pad, round(lineup.height * (row_w - 2 * pad) / lineup.width)),
                               Image.LANCZOS)
    width = max(row_w, (lineup.width + 2 * pad) if lineup else 0)
    height = len(items) * (size + label_h + pad) + pad + ((lineup.height + label_h + pad) if lineup else 0)
    sheet = Image.new("RGB", (width, height), (22, 22, 26))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for i, item in enumerate(items):
        ok = item.get("ok", True)
        draw.text((pad, y + 2), f"{'OK  ' if ok else 'FAIL'}  {item['path']}  {item.get('summary', '')}", font=font,
                  fill=(120, 220, 140) if ok else (240, 90, 100))
        for v in range(views):
            frame = frames / f"{i}_{v}.png"
            if frame.exists():
                with Image.open(frame) as img:
                    sheet.paste(img.convert("RGB"), (pad + v * (size + pad), y + label_h))
        y += size + label_h + pad
    if lineup:
        draw.text((pad, y + 2), "lineup at true scale (grid: 1 unit, faint: 0.25)", font=font, fill=(225, 225, 230))
        sheet.paste(lineup, (pad, y + label_h))
    target = out / "models.png"
    sheet.save(target)
    return target
