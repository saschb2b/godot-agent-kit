"""Helpers for Blender build scripts run by `godot-agent asset blender`.

A build script is plain Blender Python that starts from an empty scene and builds a
model; the runner exports everything to .glb afterwards. Import this module as `gab`.

Conventions
- Blender is Z-up; the exported glTF is Y-up. A model's FRONT faces -Y in Blender (it
  becomes +Z in Godot, the way towers and creeps face). 1 Blender unit = 1 Godot unit =
  one map tile. The origin is the pivot (feet, trunk base) at (0, 0, 0).
- Materials are replaced on import by the project's res://materials/<name>.tres when
  that file exists, matched by material name:
    painted     vertex colour + painted pattern (see paint()); smooth or flat shaded
    foliage_*   alpha-cut card texture (textures/foliage_*.png), tinted by vertex
                colour, swaying in the wind (see card() and bough())
    other names export as they are: use glow() for emissive eyes, runes, lanterns.
- gab.seed, gab.params (the --param values) and gab.rng (seeded random.Random) are
  set before the script runs; use gab.rng for every random choice so builds repeat.
  gab.project is the Godot project's folder (None when unknown); project_file() finds a
  file in it (a palette).
- Smooth shapes (blob, tube, loft, teardrop, lobes) are single closed surfaces built
  from rings, so they shade calmly; decals (Decals) are flat 2D shapes cut onto a
  surface's own triangles, for faces, badges and runes. Characters: see chibi.py.
- Animations: every Action becomes a glTF animation with the action's name ("walk",
  "idle"); Godot plays them through the imported AnimationPlayer.
"""

from __future__ import annotations

import colorsys
import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

PATTERNS = ("plain", "bricks", "planks", "shingles", "metal", "cobble", "cloth", "gold", "crystal", "bark",
            "leaves", "thatch", "stone")

seed = 1
params: dict = {}
rng = random.Random(1)
project: Path | None = None

X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
FWD = Vector((0, -1, 0))  # a model's front


def setup(build_seed: int, build_params: dict, project_dir: str | None = None) -> None:
    global seed, params, rng, project
    seed, params, rng = build_seed, dict(build_params), random.Random(f"blender:{build_seed}")
    project = Path(project_dir) if project_dir else None


def project_file(path: str) -> Path | None:
    """A file of the project (res://... or relative to its folder), or None when the
    build does not know its project."""
    if Path(path).is_absolute():
        return Path(path)
    if project is None:
        return None
    return project / path.removeprefix("res://")


def param(name: str, default):
    """A --param value converted to the default's type."""
    value = params.get(name, default)
    return type(default)(value) if default is not None and not isinstance(default, str) else value


# ── Scene ──

def clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 30


def link(obj: bpy.types.Object) -> bpy.types.Object:
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh_object(name: str, verts, faces, uvs=None, recalc: bool = False) -> bpy.types.Object:
    """An object from vertex and face lists; uvs is a per-face list of per-corner (u, v).
    recalc turns every face outward (consistent winding), for shapes built from rings."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    if uvs:
        layer = mesh.uv_layers.new(name="UVMap")
        i = 0
        for face_uvs in uvs:
            for uv in face_uvs:
                layer.data[i].uv = uv
                i += 1
    mesh.update()
    if recalc:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
    return link(bpy.data.objects.new(name, mesh))


def primitive(kind: str, **kwargs) -> bpy.types.Object:
    """bpy.ops.mesh.primitive_<kind>_add(**kwargs), returning the new object."""
    getattr(bpy.ops.mesh, f"primitive_{kind}_add")(**kwargs)
    return bpy.context.active_object


def select_only(obj: bpy.types.Object) -> None:
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_modifiers(obj: bpy.types.Object) -> bpy.types.Object:
    select_only(obj)
    for mod in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def apply_transform(obj: bpy.types.Object) -> bpy.types.Object:
    select_only(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def join(objects: list, name: str | None = None) -> bpy.types.Object:
    objects = [o for o in objects if o is not None]
    select_only(objects[0])
    for o in objects[1:]:
        o.select_set(True)
    bpy.ops.object.join()
    obj = bpy.context.active_object
    if name:
        obj.name = name
    return obj


def subdivide(obj: bpy.types.Object, levels: int = 1) -> bpy.types.Object:
    mod = obj.modifiers.new("Subdivision", "SUBSURF")
    mod.levels = levels
    mod.render_levels = levels
    return apply_modifiers(obj)


def displace_noise(obj: bpy.types.Object, strength: float = 0.05, size: float = 0.3) -> bpy.types.Object:
    """Organic lumps: vertices pushed along their normals by cloud noise, offset by the
    seed (through an empty as the texture origin) so each build lumps differently."""
    tex = bpy.data.textures.new(f"noise_{obj.name}", "CLOUDS")
    tex.noise_scale = size
    tex.noise_depth = 2
    origin = link(bpy.data.objects.new(f"noise_origin_{obj.name}", None))
    origin.location = (rng.uniform(-40, 40), rng.uniform(-40, 40), rng.uniform(-40, 40))
    mod = obj.modifiers.new("Displace", "DISPLACE")
    mod.texture = tex
    mod.texture_coords = "OBJECT"
    mod.texture_coords_object = origin
    mod.strength = strength
    mod.mid_level = 0.5
    apply_modifiers(obj)
    bpy.data.objects.remove(origin)
    return obj


def smooth(obj: bpy.types.Object, angle: float = 50.0) -> bpy.types.Object:
    """Smooth shading, keeping edges sharper than `angle` degrees crisp."""
    for poly in obj.data.polygons:
        poly.use_smooth = True
    obj.data.set_sharp_from_angle(angle=math.radians(angle))
    return obj


def flat(obj: bpy.types.Object) -> bpy.types.Object:
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj


def decimate(obj: bpy.types.Object, ratio: float) -> bpy.types.Object:
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.ratio = ratio
    return apply_modifiers(obj)


def triangles(obj: bpy.types.Object | None = None) -> int:
    objs = [obj] if obj else [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in objs)


# ── Materials and colour ──

def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb(color) -> tuple:
    """'#rrggbb' or an (r, g, b) tuple of 0-255 → linear 0-1 floats."""
    if isinstance(color, str):
        color = color.lstrip("#")
        color = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(_linear(c / 255.0) for c in color[:3])


def material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    return mat


def painted() -> bpy.types.Material:
    """The project's painted material: vertex colours with a painted pattern."""
    return material("painted")


def foliage(name: str = "foliage_leaves") -> bpy.types.Material:
    """An alpha-cut card material; the project maps it to res://materials/<name>.tres."""
    return material(name)


def glow(name: str, color, strength: float = 1.0, base: float = 1.0) -> bpy.types.Material:
    """An emissive material that exports as it is (eyes, runes, lanterns, crystals).
    base scales the base colour under the glow: 0.35 keeps a sunlit lamp glass amber
    instead of washing out to a pale yellow."""
    mat = material(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    lin = rgb(color)
    bsdf.inputs["Base Color"].default_value = (*lin, 1.0) if base == 1.0 else (*(c * base for c in lin), 1.0)
    bsdf.inputs["Emission Color"].default_value = (*lin, 1.0)
    bsdf.inputs["Emission Strength"].default_value = strength
    return mat


def assign(obj: bpy.types.Object, mat: bpy.types.Material) -> bpy.types.Object:
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return obj


def _color_layer(mesh):
    layer = mesh.color_attributes.get("Color")
    if layer is None:
        layer = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="CORNER")
    mesh.color_attributes.active_color = layer
    return layer


def paint(obj: bpy.types.Object, color, top=None, pattern: str = "plain", gradient: float = 1.0,
          facet: float = 0.05, ground_ao: float = 0.25, ao_height: float = 0.3, faces=None) -> bpy.types.Object:
    """Vertex colours for the painted material.

    color → top blends from the object's lowest to highest point (raised to
    `gradient`); every face gets a small brightness variation (`facet`); faces near
    the ground darken (`ground_ao`). The alpha channel carries the painted pattern
    (bricks, planks, bark, leaves...), drawn by the project's painted shader using the
    object's UVs in metres (see uv_box()). `faces` limits painting to those polygon
    indices, so one mesh can carry several colours.
    """
    mesh = obj.data
    layer = _color_layer(mesh)
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}; use one of {PATTERNS}")
    alpha = (PATTERNS.index(pattern) + 0.5) / 16
    base, peak = rgb(color), rgb(top) if top is not None else None
    world = obj.matrix_world
    zs = [(world @ v.co).z for v in mesh.vertices]
    z0, z1 = min(zs), max(zs)
    chosen = set(faces) if faces is not None else None
    for poly in mesh.polygons:
        if chosen is not None and poly.index not in chosen:
            continue
        shade = 1.0 + rng.uniform(-facet, facet)
        for loop_index in poly.loop_indices:
            vi = mesh.loops[loop_index].vertex_index
            z = (world @ mesh.vertices[vi].co).z
            c = base
            if peak is not None and z1 > z0:
                t = ((z - z0) / (z1 - z0)) ** gradient
                c = tuple(base[i] + (peak[i] - base[i]) * t for i in range(3))
            dark = 1.0 - ground_ao * max(0.0, 1.0 - z / ao_height) if ao_height > 0 else 1.0
            layer.data[loop_index].color = (*(min(1.0, ch * shade * dark) for ch in c), alpha)
    return obj


def tint(obj: bpy.types.Object, color, top=None, gradient: float = 1.0, faces=None) -> bpy.types.Object:
    """Vertex colours for foliage cards: the card texture is multiplied by this tint,
    so one painted card texture serves green, amber and red trees alike."""
    return paint(obj, color, top, "plain", gradient, facet=0.08, ground_ao=0.0, faces=faces)


def mix_hex(a: str, b: str, t: float) -> str:
    """Two '#rrggbb' swatches mixed in sRGB, as a new swatch (a blush, a shade between)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def gpl_swatches(path) -> set:
    """The '#rrggbb' swatches of a GIMP palette (.gpl); empty when the file is missing."""
    path = Path(path) if path else None
    out = set()
    if path and path.exists():
        for line in path.read_text().splitlines():
            bits = line.split()
            if len(bits) >= 3 and all(b.isdigit() for b in bits[:3]):
                out.add("#%02x%02x%02x" % tuple(int(b) for b in bits[:3]))
    return out


def hsv(color, dh: float = 0.0, ds: float = 0.0, dv: float = 0.0) -> str:
    """A hex colour shifted in hue (turns), saturation and value, for painterly variation."""
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, min(1, max(0, s + ds)), min(1, max(0, v + dv)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def uv_box(obj: bpy.types.Object, scale: float = 1.0) -> bpy.types.Object:
    """UVs in metres by projecting each face along its dominant axis, so painted
    patterns (bricks, bark) keep one size on every model."""
    mesh = obj.data
    layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    world = obj.matrix_world
    for poly in mesh.polygons:
        n = poly.normal
        ax, ay, az = abs(n.x), abs(n.y), abs(n.z)
        for loop_index in poly.loop_indices:
            co = world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
            if az >= ax and az >= ay:
                uv = (co.x, co.y)
            elif ax >= ay:
                uv = (co.y, co.z)
            else:
                uv = (co.x, co.z)
            layer.data[loop_index].uv = (uv[0] / scale, uv[1] / scale)
    return obj


def uv_cylinder(obj: bpy.types.Object, scale: float = 1.0) -> bpy.types.Object:
    """UVs in metres around the Z axis: u = angle × radius, v = height. For trunks,
    limbs and round towers, so bark and bricks wrap without stretching."""
    mesh = obj.data
    layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    world = obj.matrix_world
    for poly in mesh.polygons:
        coords = [world @ mesh.vertices[mesh.loops[li].vertex_index].co for li in poly.loop_indices]
        angles = [math.atan2(c.y, c.x) for c in coords]
        if max(angles) - min(angles) > math.pi:
            angles = [a + 2 * math.pi if a < 0 else a for a in angles]
        for li, c, a in zip(poly.loop_indices, coords, angles):
            radius = max(0.02, math.hypot(c.x, c.y))
            layer.data[li].uv = (a * radius / scale, c.z / scale)
    return obj


# ── Foliage cards ──

def card(width: float, height: float, at=(0, 0, 0), yaw: float = 0.0, tilt: float = 0.0,
         bend: float = 0.0, segments: int = 3, name: str = "card") -> bpy.types.Object:
    """An upright card (grass blades, leaf clusters seen from the side): the texture's
    bottom edge at the origin, bending back by `bend` metres at the top."""
    verts, faces, uvs = [], [], []
    for i in range(segments + 1):
        t = i / segments
        z = height * t
        y = bend * t * t
        verts += [(-width / 2, y, z), (width / 2, y, z)]
    for i in range(segments):
        a, b = 2 * i, 2 * i + 2
        faces.append((a, a + 1, b + 1, b))
        t0, t1 = i / segments, (i + 1) / segments
        uvs.append([(0, t0), (1, t0), (1, t1), (0, t1)])
    obj = mesh_object(name, verts, faces, uvs)
    obj.rotation_euler = (math.radians(tilt), 0, math.radians(yaw))
    obj.location = at
    return apply_transform(obj)


def bough(length: float, width: float, droop: float = 0.1, at=(0, 0, 0), yaw: float = 0.0,
          pitch: float = 0.0, segments: int = 3, cup: float = 0.15, name: str = "bough") -> bpy.types.Object:
    """A pine bough or leafy branch card: grows outward along +X from `at`, droops by
    `droop` metres at the tip, and cups slightly along its length (`cup`, a fraction of
    the width) so it reads as volume from above. The texture's u runs root to tip."""
    verts, faces, uvs = [], [], []
    for i in range(segments + 1):
        t = i / segments
        x = length * t
        z = -droop * t * t
        lift = cup * width * (1 - t * 0.5)
        verts += [(x, -width / 2, z + lift), (x, 0.0, z), (x, width / 2, z + lift)]
    for i in range(segments):
        a, b = 3 * i, 3 * i + 3
        t0, t1 = i / segments, (i + 1) / segments
        faces += [(a, b, b + 1, a + 1), (a + 1, b + 1, b + 2, a + 2)]
        uvs += [[(t0, 0.0), (t1, 0.0), (t1, 0.5), (t0, 0.5)], [(t0, 0.5), (t1, 0.5), (t1, 1.0), (t0, 1.0)]]
    obj = mesh_object(name, verts, faces, uvs)
    obj.rotation_euler = (0, math.radians(-pitch), math.radians(yaw))
    obj.location = at
    return apply_transform(obj)


# ── Numbers ──

def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def lerp(a, b, t: float):
    return a + (b - a) * t


def smoothstep(e0: float, e1: float, x: float) -> float:
    """0 below e0, 1 above e1, a smooth S between (e0 > e1 runs it backwards)."""
    t = clamp01((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def rot(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Matrix:
    """Degrees about X, then Y, then Z, as a 3x3."""
    return (Matrix.Rotation(math.radians(z), 3, "Z") @ Matrix.Rotation(math.radians(y), 3, "Y") @
            Matrix.Rotation(math.radians(x), 3, "X"))


def about(pivot, m3: Matrix) -> Matrix:
    """A 4x4 that turns by the 3x3 m3 about pivot."""
    pivot = Vector(pivot)
    return Matrix.Translation(pivot) @ m3.to_4x4() @ Matrix.Translation(-pivot)


def transform(obj: bpy.types.Object, m: Matrix) -> bpy.types.Object:
    """Moves a part's vertices by the 4x4 m (the object keeps an identity transform)."""
    obj.data.transform(m)
    obj.data.update()
    return obj


# ── Smooth shapes (Z up, front -Y) ──
#
# Each is one closed surface of rings, built in place and posed by whole-part transforms
# (transform, about, rot): a body that is never bent shades calmly under a toon light.

def superellipse(theta: float, power: float) -> tuple:
    """(cos, sin) of theta on a superellipse: power 2 is a circle, higher is squarer."""
    c, s = math.cos(theta), math.sin(theta)
    e = 2.0 / power
    return math.copysign(abs(c) ** e, c), math.copysign(abs(s) ** e, s)


def ring(c, rx: float, ry: float, n: int, power: float = 2.0, shape=None, z_fn=None, phase: float = 0.0) -> list:
    """n points round c in the XY plane; theta -90 degrees is the front (-Y). shape(theta)
    scales the radius, z_fn(theta) lifts a point."""
    c = Vector(c)
    out = []
    for k in range(n):
        th = phase + 2 * math.pi * k / n
        x, y = superellipse(th, power)
        m = shape(th) if shape else 1.0
        p = c + Vector((x * rx * m, y * ry * m, 0.0))
        if z_fn:
            p.z += z_fn(th)
        out.append(p)
    return out


def loft(name: str, rings: list, cap0: bool = False, cap1: bool = False, ngon: bool = False,
         closed: bool = True) -> bpy.types.Object:
    """Quads between rings of equal length; caps close an end with a fan (or one n-gon).
    closed=False leaves each ring open between its last point and its first (a cape open
    at the front)."""
    n = len(rings[0])
    verts = [Vector(p) for r in rings for p in r]
    faces = []
    for i in range(len(rings) - 1):
        for k in range(n if closed else n - 1):
            k1 = (k + 1) % n
            faces.append((i * n + k, i * n + k1, (i + 1) * n + k1, (i + 1) * n + k))
    for cap, base in ((cap0, 0), (cap1, (len(rings) - 1) * n)):
        if cap and ngon:
            faces.append(tuple(range(base, base + n)))
        elif cap:
            verts.append(sum(verts[base:base + n], Vector()) / n)
            ci = len(verts) - 1
            faces += [(ci, base + (k + 1) % n, base + k) for k in range(n)]
    return mesh_object(name, verts, faces, recalc=True)


def superellipsoid_radius(d: Vector, power: float) -> float:
    """How far along unit direction d a superellipsoid of unit radii reaches."""
    return (abs(d.x) ** power + abs(d.y) ** power + abs(d.z) ** power) ** (-1.0 / power)


def blob(name: str, c, r, segs: int = 12, rows: int = 8, power: float = 2.0, shape=None, rot=None,
         lats=None) -> bpy.types.Object:
    """A superellipsoid of radii r round c (power 2 is an ellipsoid). shape(d) scales the
    radius at unit direction d, one number or one per axis; rot (a 3x3) turns it. lats, if
    given, are the rings' latitudes in degrees (ascending, poles left out), in place of
    `rows` even ones: rings can then sit close where an outline needs them (a jaw)."""
    c = Vector(c)
    rot = rot or Matrix.Identity(3)

    def point(d):
        k = superellipsoid_radius(d, power)
        m = shape(d) if shape else 1.0
        mx, my, mz = (m, m, m) if isinstance(m, (int, float)) else m
        return c + rot @ Vector((d.x * r[0] * mx * k, d.y * r[1] * my * k, d.z * r[2] * mz * k))

    phis = [math.radians(v) for v in lats] if lats else [-math.pi / 2 + math.pi * i / rows for i in range(1, rows)]
    rows = len(phis) + 1
    verts = [point(Vector((0, 0, -1)))]
    for phi in phis:
        for k in range(segs):
            th = 2 * math.pi * k / segs - math.pi / 2
            verts.append(point(Vector((math.cos(phi) * math.cos(th), math.cos(phi) * math.sin(th), math.sin(phi)))))
    verts.append(point(Vector((0, 0, 1))))
    top = len(verts) - 1

    def ri(i, k):
        return 1 + (i - 1) * segs + (k % segs)

    faces = [(0, ri(1, k + 1), ri(1, k)) for k in range(segs)]
    for i in range(1, rows - 1):
        faces += [(ri(i, k), ri(i, k + 1), ri(i + 1, k + 1), ri(i + 1, k)) for k in range(segs)]
    faces += [(ri(rows - 1, k), ri(rows - 1, k + 1), top) for k in range(segs)]
    return mesh_object(name, verts, faces, recalc=True)


def teardrop(name: str, root, axis, length: float, width: float, depth: float, side_dir, taper: float = 0.62,
             bend: float = 0.0, segs: int = 6, rows: int = 4, cage: bool = True) -> bpy.types.Object:
    """A smooth teardrop from root along axis (a feather, an ear tuft, a leaf): round and
    full at the root, tapering to a rounded tip that curls `bend` (a share of the length)
    toward side_dir; width across side_dir, depth across the third axis. A low cage
    subdivided once, or (cage=False) a blob of segs x rows as it is."""
    a = Vector(axis).normalized()
    s = (Vector(side_dir) - a * Vector(side_dir).dot(a)).normalized()
    b = a.cross(s)
    basis = Matrix((s, b, a)).transposed()
    c = Vector(root) + a * length * 0.5
    obj = blob(name, c, (width / 2, depth / 2, length / 2), segs=segs, rows=rows,
               shape=lambda d: (1.0 - taper * smoothstep(-0.4, 1.0, d.z), 1.0 - taper * 0.6 * smoothstep(-0.4, 1.0, d.z),
                                1.0), rot=basis)
    if bend:
        for v in obj.data.vertices:
            t = max(0.0, min(1.0, (v.co - Vector(root)).dot(a) / length))
            v.co += s * bend * length * t * t
        obj.data.update()
    if cage:
        subdivide(obj, 1)
    return obj


def lobes(name: str, path, radii, stretch: float = 1.3, segs: int = 8, rows: int = 5) -> bpy.types.Object:
    """Smooth, round lobes chained along the polyline `path` (a braid, a caterpillar, a
    string of beads): one ellipsoid per radius in `radii`, each long along the path and
    overlapping its neighbours by `stretch`."""
    n = len(radii)
    pts = resample(path, 2 * n + 1)   # lobe i spans pts[2i] .. pts[2i + 2], centred on pts[2i + 1]
    objs = []
    for i, r in enumerate(radii):
        a, c, b = pts[2 * i], pts[2 * i + 1], pts[2 * i + 2]
        axis = (b - a).normalized()
        side = axis.orthogonal().normalized()
        basis = Matrix((side, axis.cross(side), axis)).transposed()
        objs.append(blob(f"{name}_{i}", c, (r, r, (b - a).length / 2 * stretch), segs=segs, rows=rows, rot=basis))
    return join(objs, name)


def catmull(points, per: int = 4) -> list:
    """A Catmull-Rom spline through points, `per` samples per span."""
    pts = [Vector(p) for p in points]
    ext = [pts[0] * 2 - pts[1]] + pts + [pts[-1] * 2 - pts[-2]]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(per):
            t = k / per
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                              (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(pts[-1])
    return out


def resample(points, n: int) -> list:
    """n points spaced evenly along the polyline `points` (its ends kept)."""
    pts = [Vector(p) for p in points]
    acc = [0.0]
    for a, b in zip(pts, pts[1:]):
        acc.append(acc[-1] + (b - a).length)
    out = []
    for k in range(n):
        s = acc[-1] * k / (n - 1)
        j = min(max(i for i in range(len(acc)) if acc[i] <= s + 1e-12), len(pts) - 2)
        t = (s - acc[j]) / max(1e-12, acc[j + 1] - acc[j])
        out.append(pts[j].lerp(pts[j + 1], min(1.0, t)))
    return out


def tube(name: str, pts, radii, sides: int = 8, cap0: bool = True, cap1: bool = True, normals=None, up=None,
         closed: bool = False) -> bpy.types.Object:
    """Rings of `sides` along a polyline (an arm, a pole, a scarf, a lock of hair). A radius
    is a number or (across, normal); 0 ends in a point. normals (one per point) or up fix
    which way a flat section's thin side faces; otherwise the frame is carried along by
    parallel transport. closed joins the last ring to the first (a ring of rope)."""
    pts = [Vector(p) for p in pts]
    n = len(pts)
    tangents = []
    for i in range(n):
        if closed:
            a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        else:
            a, b = pts[max(0, i - 1)], pts[min(n - 1, i + 1)]
        tangents.append((b - a).normalized())
    frames = []
    ref = Vector(up) if up is not None else (Z if abs(tangents[0].z) < 0.9 else X)
    for i, t in enumerate(tangents):
        if normals is not None:
            nb = Vector(normals[i])
        elif i == 0 or up is not None:
            nb = ref.copy()
        else:
            nb = frames[-1][1]
        nb = (nb - t * nb.dot(t))
        if nb.length < 1e-6:
            nb = t.orthogonal()
        nb.normalize()
        frames.append((t.cross(nb).normalized(), nb))
    verts, faces, index = [], [], []
    for p, r, (s, b) in zip(pts, radii, frames):
        ra, rb = (r, r) if not isinstance(r, (tuple, list)) else r
        if ra <= 0 and rb <= 0:
            index.append([len(verts)])
            verts.append(p)
            continue
        index.append(list(range(len(verts), len(verts) + sides)))
        for k in range(sides):
            a = 2 * math.pi * k / sides
            verts.append(p + s * math.cos(a) * ra + b * math.sin(a) * rb)
    for i in range(n if closed else n - 1):
        A, B = index[i], index[(i + 1) % n]
        for k in range(sides):
            if len(A) == 1 and len(B) > 1:
                faces.append((A[0], B[(k + 1) % sides], B[k]))
            elif len(B) == 1 and len(A) > 1:
                faces.append((A[k], A[(k + 1) % sides], B[0]))
            elif len(A) > 1:
                faces.append((A[k], A[(k + 1) % sides], B[(k + 1) % sides], B[k]))
    if cap0 and not closed and len(index[0]) > 1:
        faces.append(tuple(reversed(index[0])))
    if cap1 and not closed and len(index[-1]) > 1:
        faces.append(tuple(index[-1]))
    return mesh_object(name, verts, faces, recalc=True)


def cull(obj: bpy.types.Object, hidden) -> bpy.types.Object:
    """Deletes the faces of obj whose every vertex hidden(co) calls covered (skin under
    hair, hair under a hat), so the triangles go where they show."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    dead = [f for f in bm.faces if all(hidden(v.co) for v in f.verts)]
    bmesh.ops.delete(bm, geom=dead, context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def crease(obj: bpy.types.Object, select) -> bpy.types.Object:
    """Creases the edges select(co0, co1) accepts, so a subdivision keeps them hard (a sole)."""
    me = obj.data
    attr = me.attributes.get("crease_edge") or me.attributes.new("crease_edge", "FLOAT", "EDGE")
    for e in me.edges:
        a, b = me.vertices[e.vertices[0]].co, me.vertices[e.vertices[1]].co
        attr.data[e.index].value = 1.0 if select(a, b) else 0.0
    return obj


# ── Decals: flat shapes that lie exactly on a surface ──
#
# A decal is a list of convex 2D cells in the XZ plane (as seen from the front, along +Y),
# cut by every front-facing triangle of the surface under it and set on that triangle's
# plane, lifted a little. It therefore lies on exactly what the renderer draws, however
# coarse the surface, and stacked layers stay parallel: eyes, brows and mouths on a head,
# a badge on a shield, a rune on a stone. Cells come from ellipse(), stroke() +
# strip_cells(), or any convex polygon.

class FrontSurface:
    """Ray casts onto one object's surface (world space): a point on it seen from the front."""

    def __init__(self, obj: bpy.types.Object):
        me = obj.data
        self.bvh = BVHTree.FromPolygons([obj.matrix_world @ v.co for v in me.vertices],
                                        [tuple(p.vertices) for p in me.polygons])

    def wrap(self, x: float, z: float, lift: float, y0: float = -1.0) -> Vector:
        """Where the ray along +Y through (x, z) first meets the surface, lifted along its normal."""
        loc, nrm, _, _ = self.bvh.ray_cast(Vector((x, y0, z)), Y)
        if loc is None:
            raise RuntimeError(f"point ({x:.3f}, {z:.3f}) misses the surface")
        return loc + nrm * lift


def area2(poly) -> float:
    """Signed area of a 2D polygon (positive counter-clockwise)."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1])) / 2


def clip_convex(subject, window) -> list:
    """The 2D polygon `subject` clipped to the convex, counter-clockwise `window`."""
    out = list(subject)
    for i in range(len(window)):
        a, b = window[i], window[(i + 1) % len(window)]
        inp, out = out, []
        if not inp:
            break

        def inside(q):
            return (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) >= -1e-14

        def cut(p0, p1):
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            ex, ey = b[0] - a[0], b[1] - a[1]
            t = ((a[0] - p0[0]) * ey - (a[1] - p0[1]) * ex) / (dx * ey - dy * ex)
            return (p0[0] + dx * t, p0[1] + dy * t)

        for j in range(len(inp)):
            p0, p1 = inp[j], inp[(j + 1) % len(inp)]
            if inside(p0):
                out.append(p0)
                if not inside(p1):
                    out.append(cut(p0, p1))
            elif inside(p1):
                out.append(cut(p0, p1))
    return out


class Decals:
    """The front-facing triangles of a triangulated surface, seen along +Y, to cut decals
    from. lift_dir(co) is the direction a decal point is lifted along (the surface normal,
    or a calmer field such as a head's)."""

    def __init__(self, obj: bpy.types.Object, lift_dir):
        me = obj.data
        self.lift_dir = lift_dir
        self.tris = []
        for poly in me.polygons:
            if poly.normal.y > -0.05:
                continue
            vs = [me.vertices[i].co.copy() for i in poly.vertices]
            tri = [(v.x, v.z) for v in vs]
            if area2(tri) < 0:
                tri.reverse()
            box = (min(t[0] for t in tri), min(t[1] for t in tri), max(t[0] for t in tri), max(t[1] for t in tri))
            self.tris.append((tri, box, vs[0], poly.normal.copy()))

    def decal(self, name: str, cells, lift: float) -> bpy.types.Object:
        """One mesh from convex 2D cells ((x, z) polygons): each cut by every surface
        triangle under it, set on that triangle's plane and lifted `lift` along lift_dir."""
        verts, faces = [], []
        for cell in cells:
            cell = list(cell) if area2(cell) > 0 else list(reversed(cell))
            cb = (min(c[0] for c in cell), min(c[1] for c in cell), max(c[0] for c in cell), max(c[1] for c in cell))
            for tri, tb, a, n in self.tris:
                if tb[0] > cb[2] or tb[2] < cb[0] or tb[1] > cb[3] or tb[3] < cb[1]:
                    continue
                piece = clip_convex(cell, tri)
                if len(piece) < 3 or area2(piece) < 1e-11:
                    continue
                idx = []
                for x, z in piece:
                    q = Vector((x, a.y - (n.x * (x - a.x) + n.z * (z - a.z)) / n.y, z))
                    verts.append(q + self.lift_dir(q) * lift)
                    idx.append(len(verts) - 1)
                faces.append(idx)
        if not faces:
            raise RuntimeError(f"decal {name} misses the surface")
        return mesh_object(name, verts, faces)


def convex(poly) -> bool:
    """Whether the 2D polygon turns the same way at every corner."""
    turns = [(b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
             for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2])]
    return all(t > 1e-14 for t in turns) or all(t < -1e-14 for t in turns)


def strip_cells(lower, upper) -> list:
    """Cells filling the band between two 2D polylines of equal length: one quad per step
    where it is convex (fewer pieces once cut by the surface), else two triangles."""
    out = []
    for k in range(len(lower) - 1):
        quad = [lower[k], lower[k + 1], upper[k + 1], upper[k]]
        if convex(quad):
            out.append(quad)
        else:
            out.append([lower[k], lower[k + 1], upper[k + 1]])
            out.append([lower[k], upper[k + 1], upper[k]])
    return out


def ellipse(c, rx: float, rz: float, n: int = 12) -> list:
    """A 2D ellipse of n points round c: one convex decal cell."""
    return [(c[0] + math.cos(a) * rx, c[1] + math.sin(a) * rz) for a in [2 * math.pi * k / n for k in range(n)]]


def stroke(points, widths, cap: bool = True) -> tuple:
    """The two edges of a 2D stroke along `points` ((x, z) pairs) of the given full
    widths, with round ends when cap: returns (lower, upper) for strip_cells."""
    pts = [Vector(p) for p in points]
    n = len(pts)
    lower, upper = [], []
    length = sum((b - a).length for a, b in zip(pts, pts[1:]))
    s = 0.0
    for i in range(n):
        if i:
            s += (pts[i] - pts[i - 1]).length
        t = (pts[min(n - 1, i + 1)] - pts[max(0, i - 1)]).normalized()
        nrm = Vector((-t.y, t.x))
        hw = widths[i] / 2
        if cap:
            for d, w_end in ((s, widths[0] / 2), (length - s, widths[-1] / 2)):
                if d < w_end:
                    hw *= math.sqrt(max(0.0, 1 - ((w_end - d) / w_end) ** 2))
        hw = max(hw, 1e-5)
        lower.append(tuple(pts[i] - nrm * hw))
        upper.append(tuple(pts[i] + nrm * hw))
    return lower, upper


def cosine_steps(n: int) -> list:
    """n values in [0, 1], dense at both ends (round stroke ends need it)."""
    return [(1 - math.cos(math.pi * k / (n - 1))) / 2 for k in range(n)]


def ray_to_outline(c, d, outline) -> float:
    """Distance from 2D point c along unit d to a closed 2D polyline (inf if no hit)."""
    best = float("inf")
    for a, b in zip(outline, outline[1:] + outline[:1]):
        a, b = Vector(a), Vector(b)
        e = b - a
        den = d.x * e.y - d.y * e.x
        if abs(den) < 1e-12:
            continue
        w = a - c
        t = (w.x * e.y - w.y * e.x) / den
        u = (w.x * d.y - w.y * d.x) / den
        if t > 0 and 0 <= u <= 1:
            best = min(best, t)
    return best


# ── Rigging and animation ──

def armature(name: str, bones: list) -> bpy.types.Object:
    """bones: [(name, head, tail, parent_name or None)], heads and tails in metres."""
    data = bpy.data.armatures.new(name)
    arm = link(bpy.data.objects.new(name, data))
    select_only(arm)
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for bone_name, head, tail, parent in bones:
        bone = data.edit_bones.new(bone_name)
        bone.head, bone.tail = Vector(head), Vector(tail)
        if parent:
            bone.parent = made[parent]
        made[bone_name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def skin(mesh_obj: bpy.types.Object, arm: bpy.types.Object, groups: dict | None = None) -> None:
    """Binds a mesh to an armature: by automatic weights, or by `groups`
    {bone_name: [vertex indices]} for rigid parts (legs, jaws)."""
    if groups:
        for bone_name, indices in groups.items():
            group = mesh_obj.vertex_groups.new(name=bone_name)
            group.add(list(indices), 1.0, "REPLACE")
        mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        mesh_obj.parent = arm
        return
    select_only(mesh_obj)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")


def animate(arm: bpy.types.Object, action_name: str, frames: int, keys: dict, loop: bool = True) -> None:
    """keys: {bone_name: [(frame, (rx, ry, rz) degrees, optional (x, y, z) offset)]}.
    The first key is repeated at the end when `loop`, so cycles join without a hitch."""
    action = bpy.data.actions.new(action_name)
    arm.animation_data_create()
    arm.animation_data.action = action
    for bone_name, track in keys.items():
        pose = arm.pose.bones[bone_name]
        pose.rotation_mode = "XYZ"
        items = list(track) + ([(frames, *track[0][1:])] if loop else [])
        for key in items:
            frame, rot = key[0], key[1]
            pose.rotation_euler = tuple(math.radians(a) for a in rot)
            pose.keyframe_insert("rotation_euler", frame=frame)
            if len(key) > 2:
                pose.location = key[2]
                pose.keyframe_insert("location", frame=frame)
    track = arm.animation_data.nla_tracks.new()
    track.name = action_name
    track.strips.new(action_name, 1, action)
    arm.animation_data.action = None


# ── Export ──

def export(path: str) -> None:
    for obj in bpy.context.scene.objects:
        obj.select_set(obj.type in ("MESH", "ARMATURE"))
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", use_selection=True, export_apply=True, export_yup=True,
        export_normals=True, export_vertex_color="ACTIVE", export_all_vertex_colors=False,
        export_materials="EXPORT", export_image_format="NONE", export_animations=True,
        export_animation_mode="ACTIONS", export_optimize_animation_size=True)
