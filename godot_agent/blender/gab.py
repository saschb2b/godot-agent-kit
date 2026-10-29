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
- Animations: every Action becomes a glTF animation with the action's name ("walk",
  "idle"); Godot plays them through the imported AnimationPlayer.
"""

from __future__ import annotations

import colorsys
import math
import random

import bpy
from mathutils import Vector

PATTERNS = ("plain", "bricks", "planks", "shingles", "metal", "cobble", "cloth", "gold", "crystal", "bark",
            "leaves", "thatch", "stone")

seed = 1
params: dict = {}
rng = random.Random(1)


def setup(build_seed: int, build_params: dict) -> None:
    global seed, params, rng
    seed, params, rng = build_seed, dict(build_params), random.Random(f"blender:{build_seed}")


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


def mesh_object(name: str, verts, faces, uvs=None) -> bpy.types.Object:
    """An object from vertex and face lists; uvs is a per-face list of per-corner (u, v)."""
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


def glow(name: str, color, strength: float = 1.0) -> bpy.types.Material:
    """An emissive material that exports as it is (eyes, runes, lanterns, crystals)."""
    mat = material(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    lin = rgb(color)
    bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
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
