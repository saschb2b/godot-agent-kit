"""Chibi characters and the toon material: the helpers, the fixture build and its review renders.

    python3 -m unittest tests/test_chibi.py -v

The fixture is tests/fixture/art_src/blender/gardener.py. Tests that build need Blender on
PATH; the renders also need Godot and Xvfb. Without them those tests are skipped.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from PIL import Image  # noqa: E402

from godot_agent.assets import meshes, pipeline  # noqa: E402
from godot_agent.env import GodotAgentError  # noqa: E402

HAS_BLENDER = shutil.which("blender") is not None
HAS_DISPLAY = shutil.which("Xvfb") is not None

# Runs inside Blender: the shape and decal helpers, the face spec, the hat. Prints one RESULT line.
HELPER_PROBE = """
import json, sys
sys.path.insert(0, {blender_dir!r})
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import gab, chibi
gab.setup(1, {{}}, None)
gab.clear()
out = {{}}

def open_edges(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    return n

out["clip_area"] = gab.area2(gab.clip_convex([(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 0), (1, 0), (0, 1)]))
lo, up = gab.stroke([(x / 10, 0.0) for x in range(11)], [0.1] * 11)
cells = gab.strip_cells(lo, up)
out["strip_convex"] = all(gab.convex(c) for c in cells)
out["strip_area"] = sum(abs(gab.area2(c)) for c in cells)
out["closed"] = [open_edges(gab.blob("b", (0, 0, 0), (0.2, 0.3, 0.4), segs=10, rows=6)),
                 open_edges(gab.tube("t", [(0, 0, 0), (0, 0, 0.5), (0.2, 0, 0.8)], [0.05, 0.04, 0.0])),
                 open_edges(gab.loft("l", [gab.ring((0, 0, z), 0.1, 0.1, 8) for z in (0, 0.1, 0.2)], True, True)),
                 open_edges(gab.teardrop("d", (0, 0, 0), (0, 0, 1), 0.2, 0.08, 0.03, (1, 0, 0), bend=0.2)),
                 open_edges(gab.lobes("p", [(0, 0, 0), (0, 0, 0.3)], [0.04, 0.03, 0.02]))]
ball = gab.blob("ball", (0, 0, 1), (0.3, 0.3, 0.3), segs=24, rows=12)
bm = bmesh.new(); bm.from_mesh(ball.data); bmesh.ops.triangulate(bm, faces=bm.faces[:]); bm.to_mesh(ball.data); bm.free()
spot = gab.Decals(ball, lambda co: (co - Vector((0, 0, 1))).normalized()).decal(
    "spot", [gab.ellipse((0.05, 1.02), 0.08, 0.06, 12)], 0.002)
bvh = BVHTree.FromPolygons([v.co for v in ball.data.vertices], [tuple(p.vertices) for p in ball.data.polygons])
lifts = [bvh.find_nearest(v.co)[3] for v in spot.data.vertices]
out["decal_lift"] = [min(lifts), max(lifts)]
out["decal_front"] = max(v.co.y for v in spot.data.vertices) < 0.0
f = chibi.face_spec({{"expression": "surprised", "mouth_w": 0.2}})
out["face_missing"] = sorted(set(chibi.FACE_DEFAULTS) - set(f))
out["face"] = [f["mouth"], f["mouth_w"], f["brow_raise"], f["eye_w"]]
try:
    chibi.face_spec({{"expression": "grumpy"}})
    out["bad_expression"] = None
except ValueError as e:
    out["bad_expression"] = str(e)
fig = chibi.Figure({{"H": 0.3, "heads": 2.7, "skin": "#e8b48c", "blush": "#ee8f80", "face": {{}}}})
fig.head()
fig.face()
hat = fig.hat(fig.HC + Vector((0, 0, 0.07)), [(0, 1.0), (0.05, 1.0), (0.1, 0.6), (0.11, 0.0)], 0.16, "#e6c072",
              band="#c0443a", brim=[(0, 0.006), (0.94, 0.003), (0.97, -0.010)], r_out=0.3,
              tilt=chibi.about(fig.HC, chibi.rot(x=-10)))
out["crown_open"] = open_edges(hat.crown)
out["brim_open"] = open_edges(hat.brim)
out["covers"] = [hat.covers(fig.HC + Vector((0, 0, 0.3))), hat.covers(fig.HC)]
out["parts"] = sorted({{p for _, p in fig.parts}})
out["face_pieces"] = sum(1 for _, p in fig.parts if p == "face")
out["materials"] = sorted({{o.data.materials[0].name for o, _ in fig.parts}})
print("RESULT " + json.dumps(out))
"""

# A figure that cannot meet its budget: finish(check=True) must fail the build.
OVER_BUDGET = """
import chibi
fig = chibi.Figure({"H": 0.3, "heads": 2.7, "skin": "#e8b48c", "blush": "#ee8f80", "face": {},
                    "budget": {"full": 100, "bust": 100}})
fig.head()
fig.face()
fig.finish("tiny", check=True)
"""

# Prints the material Godot gives an imported model.
MATERIAL_PROBE = """
extends SceneTree
func _initialize():
    var node: Node = load("res://models/%s").instantiate()
    var mi: MeshInstance3D = node.find_children("*", "MeshInstance3D", true, false)[0]
    var material: Material = mi.get_active_material(0)
    var shader: Shader = material.shader if material is ShaderMaterial else null
    print("MATERIAL=", material.resource_path, " SHADER=", shader.resource_path if shader else "none")
    node.free()
    quit()
"""


def _fixture_copy() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="godot-agent-chibi-"))
    project = tmp / "project"
    shutil.copytree(KIT / "tests" / "fixture", project, ignore=shutil.ignore_patterns(".godot"))
    return tmp, project


def _probe_material(project: Path, model: str) -> str:
    from godot_agent.env import find_godot
    probe = project / "probe_material.gd"
    probe.write_text(MATERIAL_PROBE % model)
    proc = subprocess.run([find_godot(), "--headless", "--path", str(project), "-s", str(probe)],
                          capture_output=True, text=True, timeout=120)
    return next((line for line in proc.stdout.splitlines() if line.startswith("MATERIAL=")), proc.stdout + proc.stderr)


class ToonImportTest(unittest.TestCase):
    """The toon preset without Blender: a model whose material is named toon."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.project = _fixture_copy()
        mesh = meshes.build_mesh(meshes.preset_recipe("rock"), 1)
        with mock.patch.object(meshes, "PAINTED_MATERIAL", "toon"):
            meshes.write_glb(mesh, cls.project / "models" / "hero.glb", "hero")
        with mock.patch.object(meshes, "PAINTED_MATERIAL", "hero_toon"):
            meshes.write_glb(mesh, cls.project / "models" / "villain.glb", "villain")
        meshes.write_glb(mesh, cls.project / "models" / "rock.glb", "rock")
        style = pipeline.Style.load(cls.project)
        for rel in ("models/hero.glb", "models/villain.glb", "models/rock.glb"):
            pipeline._toon_materials(cls.project, rel)
            pipeline._model_import_file(cls.project, rel, pipeline._existing_material(cls.project, style))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_names(self):
        self.assertTrue(all(pipeline.is_toon(n) for n in ("toon", "toon_glass", "builder_toon")))
        self.assertFalse(any(pipeline.is_toon(n) for n in ("painted", "cartoon", "toonish", "foliage_leaves")))

    def test_a_toon_material_gets_the_kit_shader_and_no_lods(self):
        tres = (self.project / "materials" / "toon.tres").read_text()
        self.assertIn('resource_name = "toon"', tres)
        self.assertIn("res://materials/toon.gdshader", tres)
        shader = (self.project / "materials" / "toon.gdshader").read_text()
        self.assertTrue(shader.startswith("// godot-agent toon"))
        self.assertIn("void light()", shader)
        mapping = (self.project / "models" / "hero.glb.import").read_text()
        self.assertIn('"use_external/path": "res://materials/toon.tres"', mapping)
        self.assertIn("meshes/generate_lods=false", mapping)

    def test_a_projects_own_toon_material_is_left_alone(self):
        self.assertFalse((self.project / "materials" / "hero_toon.tres").exists())
        self.assertIn("meshes/generate_lods=false", (self.project / "models" / "villain.glb.import").read_text())

    def test_painted_models_keep_their_lods(self):
        self.assertNotIn("generate_lods=false", (self.project / "models" / "rock.glb.import").read_text())

    def test_godot_loads_the_toon_material(self):
        pipeline.godot_import(self.project)
        self.assertIn("MATERIAL=res://materials/toon.tres SHADER=res://materials/toon.gdshader",
                      _probe_material(self.project, "hero.glb"))


@unittest.skipUnless(HAS_BLENDER, "blender not installed")
class ChibiHelpersTest(unittest.TestCase):
    """The helpers themselves, inside Blender."""

    @classmethod
    def setUpClass(cls):
        from godot_agent.runner import render_slot
        cls.tmp = Path(tempfile.mkdtemp(prefix="godot-agent-chibi-probe-"))
        probe = cls.tmp / "probe.py"
        probe.write_text(HELPER_PROBE.format(blender_dir=str(KIT / "godot_agent" / "blender")))
        with render_slot():
            proc = subprocess.run(["blender", "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(probe)],
                                  capture_output=True, text=True, timeout=300)
        line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
        if line is None:
            raise AssertionError("probe failed:\n" + (proc.stdout + proc.stderr)[-3000:])
        cls.out = json.loads(line[len("RESULT "):])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_clipping_and_strokes(self):
        self.assertAlmostEqual(self.out["clip_area"], 0.5)
        self.assertTrue(self.out["strip_convex"])
        # a 1.0 x 0.1 stroke with round ends: less than the rectangle, more than its middle
        self.assertGreater(self.out["strip_area"], 0.09)
        self.assertLess(self.out["strip_area"], 0.1)

    def test_smooth_shapes_are_closed(self):
        self.assertEqual(self.out["closed"], [0, 0, 0, 0, 0])

    def test_a_decal_lies_its_lift_above_the_surface(self):
        lo, hi = self.out["decal_lift"]
        self.assertGreater(lo, 0.0019)
        self.assertLess(hi, 0.0021)
        self.assertTrue(self.out["decal_front"], "cast from the front, onto the front")

    def test_a_face_is_defaults_then_expression_then_own_keys(self):
        self.assertEqual(self.out["face_missing"], [])
        self.assertEqual(self.out["face"][:3], ["o", 0.2, 0.026])
        self.assertIn("grumpy", self.out["bad_expression"])
        self.assertIn("cheerful", self.out["bad_expression"])

    def test_a_hat_is_a_crown_and_a_closed_brim(self):
        self.assertEqual(self.out["crown_open"], 20, "open only round its bottom ring")
        self.assertEqual(self.out["brim_open"], 0)
        self.assertEqual(self.out["covers"], [True, False])

    def test_the_face_is_thirteen_decals_and_a_nose(self):
        self.assertEqual(self.out["parts"], ["face", "hat", "head"])
        self.assertEqual(self.out["face_pieces"], 14)
        self.assertEqual(self.out["materials"], ["toon"])


@unittest.skipUnless(HAS_BLENDER, "blender not installed")
class ChibiFixtureTest(unittest.TestCase):
    """The gardener fixture through `asset blender`, and its character sheet."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.project = _fixture_copy()
        cls.full = pipeline.make_blender(str(cls.project), "art_src/blender/gardener.py", "res://models/gardener.glb",
                                         params={"check": "1"}, import_into_godot=False)
        cls.bust = pipeline.make_blender(str(cls.project), "art_src/blender/gardener.py",
                                         "res://models/gardener_bust.glb", params={"check": "1", "bust": "1"})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_toon_mesh_in_budget_standing_on_the_origin(self):
        for result, limit in ((self.full, 4000), (self.bust, 3000)):
            self.assertTrue(result["ok"], result["problems"])
            m = result["metrics"]
            self.assertEqual(m["material_names"], ["toon"])
            self.assertLessEqual(m["tris"], limit)
            self.assertAlmostEqual(m["min"][1], 0.0, places=3)
        self.assertLess(self.bust["metrics"]["size"][1], self.full["metrics"]["size"][1] * 0.75)
        recipe = json.loads((self.project / "asset_manifest.json").read_text())["assets"]["models/gardener_bust.glb"]
        self.assertEqual((recipe["tool"], recipe["params"]), ("blender", {"check": "1", "bust": "1"}))

    def test_vertex_alpha_carries_the_shade_classes(self):
        gltf, buffers = meshes._read_gltf(self.project / "models" / "gardener.glb")
        prim = gltf["meshes"][0]["primitives"][0]
        alphas = {round(c[3], 2) for c in meshes._accessor(gltf, buffers, prim["attributes"]["COLOR_0"])}
        self.assertEqual(alphas, {0.25, 0.5, 0.75, 1.0})

    def test_toon_material_imports_without_lods(self):
        mapping = (self.project / "models" / "gardener_bust.glb.import").read_text()
        self.assertIn('"use_external/path": "res://materials/toon.tres"', mapping)
        self.assertIn("meshes/generate_lods=false", mapping)
        self.assertIn("MATERIAL=res://materials/toon.tres SHADER=res://materials/toon.gdshader",
                      _probe_material(self.project, "gardener_bust.glb"))

    def test_check_fails_a_figure_over_its_budget(self):
        script = self.tmp / "tiny.py"
        script.write_text(OVER_BUDGET)
        with self.assertRaises(GodotAgentError) as caught:
            pipeline.make_blender(str(self.project), str(script), "res://models/tiny.glb", import_into_godot=False)
        self.assertIn("over the 100 budget", str(caught.exception))

    def test_an_unknown_expression_names_the_known_ones(self):
        with self.assertRaises(GodotAgentError) as caught:
            pipeline.make_blender(str(self.project), "art_src/blender/gardener.py", "res://models/grumpy.glb",
                                  params={"expression": "grumpy"}, import_into_godot=False)
        self.assertIn("unknown expression 'grumpy'", str(caught.exception))

    @unittest.skipUnless(HAS_DISPLAY, "rendering needs Xvfb")
    def test_turnaround_sheet_has_views_silhouettes_and_the_face(self):
        out = self.tmp / "turn"
        sheet = meshes.render_turnaround(self.project, [{"path": "models/gardener.glb"}], out,
                                         material=pipeline._existing_material(self.project,
                                                                              pipeline.Style.load(self.project)),
                                         focus=0.647, face_size=0.48)
        self.assertTrue(sheet.exists())
        frames = out / "frames"
        sizes = {Image.open(frames / f"0_turn_{v}.png").size for v in range(4)}
        self.assertEqual(sizes, {(300, 440)}, "every view from one camera, one size")
        for name in ("0_sil_0", "0_sil_2", "0_sil_top"):
            with Image.open(frames / f"{name}.png") as img:
                grey = img.convert("L")
                values = grey.tobytes()
                black = sum(v < 40 for v in values) / len(values)
                self.assertGreater(black, 0.08, name)
                self.assertLess(black, 0.6, name)
                self.assertGreater(grey.getpixel((2, 2)), 240, f"{name}: white ground")
                box = grey.point(lambda v: 255 if v < 40 else 0).getbbox()
                self.assertTrue(box[0] > 0 and box[2] < grey.width, f"{name}: the whole figure is in frame")
        # front and back silhouettes are about the same width: the figure turns about its own
        # axis (a turntable off its axis would differ by tens of pixels; the watering can's
        # spout, reaching out toward the front camera, widens the front view by a few)
        widths = []
        for v in (0, 3):
            with Image.open(frames / f"0_sil_{v}.png") as img:
                box = img.convert("L").point(lambda p: 255 if p < 40 else 0).getbbox()
                widths.append(box[2] - box[0])
        self.assertLess(abs(widths[0] - widths[1]), 12)
        # the face close-up is mostly the head: skin and paint, not background
        with Image.open(frames / "0_face_0.png") as img:
            centre = img.convert("RGB").crop((120, 120, 240, 240))
            background = (0x2b, 0x26, 0x22)
            data = centre.tobytes()
            near_bg = sum(1 for i in range(0, len(data), 3)
                          if sum(abs(data[i + k] - background[k]) for k in range(3)) < 12)
            self.assertLess(near_bg, 120 * 120 * 0.05)


if __name__ == "__main__":
    unittest.main()
