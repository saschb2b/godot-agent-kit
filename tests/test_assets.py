"""Asset pipeline self-test against a temporary copy of the fixture project.

    python3 -m unittest tests/test_assets.py -v

Needs Godot and Pillow; the Aseprite test is skipped when aseprite is missing.
"""

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from PIL import Image, ImageDraw  # noqa: E402

from godot_agent.assets import manifest, pipeline  # noqa: E402

PICO8 = ["#000000", "#1d2b53", "#7e2553", "#008751", "#ab5236", "#5f574f", "#c2c3c7", "#fff1e8",
         "#ff004d", "#ffa300", "#ffec27", "#00e436", "#29adff", "#83769c", "#ff77a8", "#ffccaa"]
STYLE = {
    "version": 1,
    "background": "#1d2b53",
    "palettes": {"pico8": PICO8},
    "rules": [
        {"match": "art/**/*.png", "image": {"size": [16, 16], "palette": "pico8", "binary_alpha": True,
                                             "min_silhouette_contrast": 1.5, "max_orphan_pixels": 0}},
        {"match": "art/anim/*.png", "image": {"size": None, "frame": [16, 16]}},
        {"match": "sfx/*.wav", "audio": {"max_peak_db": -0.5, "max_leading_silence_ms": 10,
                                         "sample_rates": [44100], "channels": 1, "max_duration_s": 1.5}},
        {"match": "models/*.glb", "model": {"max_tris": 400, "origin": "bottom", "max_size": [1.5, 2.0, 1.5],
                                            "colors": {"leaf": "#ff004d"}}},
        {"match": "models/tiny/*.glb", "model": {"max_tris": 10}},
        {"match": "icons/*.png", "image": {"size": [48, 48]}},
    ],
}

# Prints which material Godot gives the imported model (it must be the painted shader).
VERTEX_COLOR_PROBE = """
extends SceneTree
func _initialize():
    var node: Node = load("res://models/tree.glb").instantiate()
    var mi: MeshInstance3D = node.find_children("*", "MeshInstance3D", true, false)[0]
    var material: Material = mi.get_active_material(0)
    print("MATERIAL=", material.resource_path, " ", material.get_class())
    node.free()
    quit()
"""

# A Blender build script: a painted post, a grass card and a two-bone rig with a walk.
BLENDER_SCRIPT = """
import gab
post = gab.primitive("cylinder", vertices=8, radius=0.1, depth=0.6, location=(0, 0, 0.3))
gab.assign(post, gab.painted())
gab.paint(post, "#ff004d", pattern="bark")
gab.uv_cylinder(post)
tuft = gab.assign(gab.card(0.4, 0.3, at=(0.2, 0, 0)), gab.foliage("foliage_grass"))
gab.tint(tuft, "#008751", top="#00e436")
arm = gab.armature("rig", [("root", (0, 0, 0), (0, 0, 0.3), None), ("top", (0, 0, 0.3), (0, 0, 0.6), "root")])
gab.skin(post, arm, {"root": range(0, 16)})
gab.animate(arm, "walk", 12, {"top": [(0, (0, 0, 0)), (6, (10, 0, 0))]})
"""

# Builds a 4-frame, two-tag sprite: a red pixel walking right, idle and pingpong walk.
ASEPRITE_SCRIPT = """
local spr = Sprite(16, 16, ColorMode.RGB)
for i = 2, 4 do spr:newEmptyFrame() end
for i, frame in ipairs(spr.frames) do
  local img = Image(16, 16, ColorMode.RGB)
  for y = 4, 11 do for x = 2 + i, 7 + i do img:drawPixel(x, y, Color{r=255, g=0, b=77}) end end
  spr:newCel(spr.layers[1], frame, img)
  frame.duration = (i == 4) and 0.2 or 0.1
end
local idle = spr:newTag(1, 2); idle.name = "idle"
local walk = spr:newTag(3, 4); walk.name = "walk"; walk.aniDir = AniDir.PING_PONG
spr:saveAs(app.params.out)
"""


class AssetPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="godot-agent-assets-"))
        cls.project = cls.tmp / "project"
        shutil.copytree(KIT / "tests" / "fixture", cls.project, ignore=shutil.ignore_patterns(".godot"))
        (cls.project / "asset_style.json").write_text(json.dumps(STYLE, indent=2))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_conform_turns_a_smooth_drawing_into_a_valid_sprite(self):
        # A large anti-aliased drawing in off-palette colors: everything the style forbids.
        source = self.tmp / "orb_drawing.png"
        img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((96, 96, 416, 416), fill=(250, 120, 40, 255))
        draw.ellipse((180, 150, 280, 250), fill=(255, 230, 120, 200))
        img.save(source)
        result = pipeline.conform(str(self.project), str(source), "res://art/orb.png", outline="auto",
                                  import_into_godot=False)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["metrics"]["size"], [16, 16])
        self.assertTrue(Path(result["preview"]).exists())

    def test_regen_reruns_a_project_script(self):
        # A project's own painter, recorded as a script entry, is rerun by regen.
        script = self.project / "art_src" / "paint.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        (self.project / "art").mkdir(exist_ok=True)
        script.write_text("import sys\nfrom PIL import Image\nImage.new('RGBA', (8, 8), sys.argv[2]).save(sys.argv[1])\n")
        manifest.record(self.project, "art/painted.png", {"tool": "script", "script": "art_src/paint.py",
                                                          "regen": "python3 art_src/paint.py art/painted.png #c08040"})
        result = pipeline.regen(str(self.project), "art/painted.png")
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(Image.open(self.project / "art" / "painted.png").getpixel((4, 4)), (192, 128, 64, 255))
        manifest.record(self.project, "art/unwritten.png", {"tool": "script", "regen": "python3 -c pass"})
        self.assertFalse(pipeline.regen(str(self.project), "art/unwritten.png")["ok"])

    def test_check_flags_a_broken_sprite(self):
        broken = self.project / "art" / "broken.png"
        broken.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (20, 16), (0, 0, 0, 0))
        img.putpixel((3, 3), (10, 200, 10, 128))  # off palette, semi-transparent, stray, wrong size
        img.save(broken)
        report = pipeline.check_assets(str(self.project), ["art/broken.png"])
        problems = " | ".join(report["results"][0]["problems"])
        for expected in ("size 20x16", "semi-transparent", "outside palette", "stray"):
            self.assertIn(expected, problems)
        broken.unlink()

    def test_sfx_is_valid_and_regenerates_byte_identical(self):
        result = pipeline.make_sfx(str(self.project), "pickup", "res://sfx/coin.wav", seed=7, variants=2,
                                   import_into_godot=False)
        self.assertEqual(len(result["results"]), 2)
        for r in result["results"]:
            self.assertTrue(r["ok"], r["problems"])
        first = (self.project / "sfx" / "coin_1.wav").read_bytes()
        self.assertNotEqual(first, (self.project / "sfx" / "coin_2.wav").read_bytes())
        pipeline.regen(str(self.project), "sfx/coin_1.wav")
        self.assertEqual(first, (self.project / "sfx" / "coin_1.wav").read_bytes())

    @unittest.skipUnless(shutil.which("aseprite"), "aseprite not installed")
    def test_aseprite_import_writes_loadable_sprite_frames(self):
        script = self.tmp / "make.lua"
        script.write_text(ASEPRITE_SCRIPT)
        source = self.tmp / "walker.aseprite"
        subprocess.run(["aseprite", "-b", "--script-param", f"out={source}", "--script", str(script)],
                       check=True, capture_output=True, timeout=120)
        result = pipeline.import_aseprite(str(self.project), str(source), "res://art/anim/walker")
        self.assertTrue(result["ok"], result["problems"])
        animations = {a["name"]: a for a in result["animations"]}
        self.assertEqual(animations["idle"]["frames"], 2)
        self.assertEqual(animations["walk"]["frames"], 2)  # pingpong over 2 frames adds none
        self.assertEqual(animations["idle"]["fps"], 10.0)
        tres = (self.project / "art" / "anim" / "walker.tres").read_text()
        self.assertIn('"name": &"walk"', tres)
        self.assertIn('"duration": 2.0', tres)  # the 200 ms frame lasts twice the 100 ms base


    def test_every_mesh_preset_builds_valid_and_regenerates_identical(self):
        from godot_agent.assets import meshes
        for preset in meshes.PRESETS:
            result = pipeline.make_mesh(str(self.project), preset, f"res://models/{preset}.glb", seed=3,
                                        import_into_godot=False)
            for r in result["results"]:
                self.assertTrue(r["ok"], f"{preset}: {r['problems']}")
        first = (self.project / "models" / "rock.glb").read_bytes()
        pipeline.regen(str(self.project), "models/rock.glb")
        self.assertEqual(first, (self.project / "models" / "rock.glb").read_bytes())
        # The style rule's named colors reach the model: leaves in the fixture palette's red.
        colors = meshes.analyze_model(self.project / "models" / "tree_round.glb")["vertex_colors"]
        self.assertTrue(any(meshes._paint_matches(c, [(255, 0, 77)], 30) for c in colors))

    def test_model_check_flags_rule_breaks(self):
        pipeline.make_mesh(str(self.project), "bush", "res://models/tiny/bush.glb", import_into_godot=False)
        report = pipeline.check_assets(str(self.project), ["models/tiny/bush.glb"])
        problems = " | ".join(report["results"][0]["problems"])
        self.assertIn("triangles, max 10", problems)

    def test_imported_model_shows_its_vertex_colors(self):
        pipeline.make_mesh(str(self.project), "tree_round", "res://models/tree.glb")
        self.assertIn('"use_external/path": "res://materials/painted.tres"',
                      (self.project / "models" / "tree.glb.import").read_text())
        probe = self.tmp / "probe.gd"
        probe.write_text(VERTEX_COLOR_PROBE.replace("    ", "\t"))
        out = subprocess.run(["godot", "--headless", "--path", str(self.project), "-s", str(probe)],
                             capture_output=True, text=True, timeout=120).stdout
        self.assertIn("MATERIAL=res://materials/painted.tres ShaderMaterial", out)

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb not installed")
    def test_icon_from_model_is_transparent_around_the_model(self):
        pipeline.make_mesh(str(self.project), "pumpkin", "res://models/pumpkin.glb", import_into_godot=False)
        result = pipeline.make_icon(str(self.project), "res://models/pumpkin.glb", "res://icons/pumpkin.png",
                                    size=48, import_into_godot=False)
        self.assertTrue(result["ok"], result["problems"])
        with Image.open(self.project / "icons" / "pumpkin.png") as icon:
            self.assertEqual(icon.mode, "RGBA")
            self.assertEqual(icon.getpixel((0, 0))[3], 0)
            self.assertEqual(icon.getpixel((24, 26))[3], 255)


    def test_patterns_and_bevels_reach_the_gltf(self):
        from godot_agent.assets import meshes
        recipe = {"colors": {"stone": "#8a8580"}, "parts": [
            {"shape": "box", "size": [0.6, 0.8, 0.6], "bevel": 0.05, "color": "stone", "pattern": "bricks"},
            {"shape": "cylinder", "radius": 0.3, "height": 0.2, "sides": 8, "bevel": 0.03, "at": [0, 0.8, 0],
             "color": "stone", "pattern": "shingles"}]}
        mesh = meshes.build_mesh(recipe, 1)
        surface = mesh["surfaces"][0]
        # Chamfered box: 6 sides, 12 edge strips, 8 corners = 44 triangles. Bevelled 8-sided
        # cylinder: 3 bands of 8 quads plus two 8-gon caps = 60.
        self.assertEqual(len(surface["positions"]) // 3, 44 + 60)
        patterns = {round(c[3] * 16 - 0.5) for c in surface["colors"]}
        self.assertEqual(patterns, {meshes.PATTERNS.index("bricks"), meshes.PATTERNS.index("shingles")})
        # UVs are in metres: the box walls reach v = 0.8, their height.
        self.assertAlmostEqual(max(uv[1] for uv in surface["uvs"]), 0.8, places=2)

    def test_painted_material_is_a_shader_and_replaces_the_old_one(self):
        old = self.project / "materials" / "painted.tres"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(pipeline._OLD_PAINTED_MATERIAL_TRES)
        pipeline.make_mesh(str(self.project), "rock", "res://models/rock2.glb", import_into_godot=False)
        self.assertIn('type="ShaderMaterial"', old.read_text())
        shader = (self.project / "materials" / "painted.gdshader").read_text()
        self.assertTrue(shader.startswith("// godot-agent painted"))

    def test_ui_frames_load_as_styleboxes(self):
        result = pipeline.make_ui(str(self.project), "panel", "res://ui/console", [128, 96])
        self.assertTrue(result["ok"], result.get("problems"))
        result = pipeline.make_ui(str(self.project), "button", "res://ui/button", [48, 48], fill="slate")
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertEqual(sum(f.endswith(".tres") for f in result["files"]), 4)
        tres = (self.project / "ui" / "console.tres").read_text()
        self.assertIn("axis_stretch_horizontal = 2", tres)
        with Image.open(self.project / "ui" / "console.png") as panel:
            self.assertEqual(panel.size, (128, 96))
            self.assertEqual(panel.getpixel((64, 48))[3], 255)

    def test_console_paints_every_piece_from_a_spec(self):
        spec = self.project / "art" / "console.json"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(json.dumps({"wing": {"well": [16, 14, 160, 104]}, "strip": {"cap": 40, "period": 48}}))
        result = pipeline.make_ui(str(self.project), "console", "res://ui/hud", [96, 96], spec="art/console.json")
        self.assertTrue(result["ok"], result.get("problems"))
        for name in ("wing_left.png", "wing_right.png", "crest.png", "rail.png", "strip.tres", "tooltip.tres",
                     "button_hover.tres", "banner.png", "plate.png", "layout.json"):
            self.assertIn(f"ui/hud/{name}", result["files"])
        layout = json.loads((self.project / "ui" / "hud" / "layout.json").read_text())
        self.assertEqual(layout["map_well"], [16, 154 - 14 - 104, 160, 104],
                         "wells are given from the top of their piece")
        self.assertEqual(layout["card_well"][0], 240 - 16 - 160, "the right wing is mirrored")
        x, y, w, h = layout["window"]
        with Image.open(self.project / "ui" / "hud" / "crest.png") as crest:
            self.assertLess(crest.getpixel((x + w // 2, y + h - 10))[3], 16, "the portrait window is clear")
        with Image.open(self.project / "ui" / "hud" / "wing_left.png") as wing:
            wx, wy, ww, wh = layout["map_well"]
            self.assertEqual(wing.getpixel((wx + ww // 2, wy + wh // 2))[3], 255, "the minimap well is solid")
            self.assertEqual(wing.getpixel((wing.width - 4, 10))[3], 0, "the wing dips to the rail")
        tres = (self.project / "ui" / "hud" / "strip.tres").read_text()
        self.assertIn("texture_margin_left = 40.0", tres)
        self.assertIn("axis_stretch_horizontal = 1", tres)
        with Image.open(self.project / "ui" / "hud" / "strip.png") as strip:
            px = strip.convert("RGB").load()
            # The bar repeats from the cap to the end, so the strip can grow without a seam.
            gap = sum(sum(abs(px[40, y][c] - px[strip.width - 1, y][c]) for c in range(3)) for y in range(30))
            step = sum(sum(abs(px[40, y][c] - px[41, y][c]) for c in range(3)) for y in range(30))
            self.assertLess(gap, step * 2 + 60)
        entry = json.loads((self.project / "asset_manifest.json").read_text())["assets"]["ui/hud/crest.png"]
        self.assertEqual(entry["options"], {"spec": "art/console.json"})

    def test_console_paints_hd_art_at_a_scale(self):
        spec = self.project / "art" / "console_hd.json"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(json.dumps({"scale": 2}))
        result = pipeline.make_ui(str(self.project), "console", "res://ui/hd", [96, 96], spec="art/console_hd.json",
                                  import_into_godot=False)
        self.assertTrue(result["ok"], result.get("problems"))
        layout = json.loads((self.project / "ui" / "hd" / "layout.json").read_text())
        self.assertEqual(layout["scale"], 2)
        self.assertEqual(layout["wing"], [240, 154], "the layout stays in layout pixels")
        with Image.open(self.project / "ui" / "hd" / "wing_left.png") as wing:
            self.assertEqual(wing.size, (480, 308), "the art carries twice the texels")
        tres = (self.project / "ui" / "hd" / "strip.tres").read_text()
        self.assertIn("texture_margin_left = 88.0", tres, "nine-slice margins are in texels")
        self.assertIn("mipmaps/generate=true", (self.project / "ui" / "hd" / "crest.png.import").read_text(),
                      "art drawn smaller than its texels gets mipmaps")

    def test_console_rim_is_one_line_and_meets_the_arch_in_a_fillet(self):
        from godot_agent.assets import ui
        # The arch (centre 116, radius 76) meets a shoulder at 108 on a fillet: the profile
        # never steps, and right of the arch it eases down to the shoulder.
        arc = ui._fillet(116, 80, 76, 108, 10, 1)
        xs = [x / 4 for x in range(760, 800)]
        heights = [max(80 + math.sqrt(max(0.0, 76 ** 2 - (x - 116) ** 2)), 108, arc(x)) for x in xs]
        steps = [a - b for a, b in zip(heights, heights[1:])]
        self.assertLess(max(steps), 3.0, "no stair-step where the arch meets the shoulder")
        self.assertGreater(arc(190), 108, "the fillet fills the inside corner")
        spec = self.project / "art" / "console_rim.json"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(json.dumps({"rail": 28, "shadow": 14}))
        pipeline.make_ui(str(self.project), "console", "res://ui/rim", [96, 96], spec="art/console_rim.json",
                         import_into_godot=False)
        with Image.open(self.project / "ui" / "rim" / "rail.png") as rail:
            # Down from the rail's top edge: bright bronze, then stone, once; no second bead.
            px = rail.convert("RGBA").load()
            column = [sum(px[32, y][:3]) for y in range(rail.height) if px[32, y][3] == 255]
        # Count the highlights: rises of more than 30 after a fall, while still brighter
        # than the stone (a second bead inside the rim would make two).
        stone = sorted(column)[len(column) // 2]
        peaks, low, rising = 0, column[0], True
        for a, b in zip(column, column[1:]):
            if b < a:
                if rising and a > stone * 1.5 and a - low > 30:
                    peaks += 1
                rising = False
                low = min(low, b) if not rising else b
            elif b > a and not rising:
                rising, low = True, a
        self.assertEqual(peaks, 1, f"one bright rim, not two: {column}")

    def test_command_icon_hue_bevel_and_foot(self):
        from godot_agent.assets import ui
        subject = Image.new("RGBA", (216, 216), (0, 0, 0, 0))
        ImageDraw.Draw(subject).rectangle((90, 10, 126, 206), fill=(230, 230, 230, 255))
        icon = ui.icon_frame(subject, trim="hue", background="#3d6cb4", edge="#0a1226", size=108, foot=0.22)
        self.assertEqual(icon.size, (108, 108))
        r, g, b, _ = icon.getpixel((0, 54))
        self.assertLess(r + g + b, 40, "a dark line round the edge")
        self.assertGreater(b, r, "in the backdrop's own hue, not a metal trim")
        self.assertGreater(sum(icon.getpixel((54, 3))[:3]), sum(icon.getpixel((54, 104))[:3]),
                           "a light catch at the top")
        # The subject stays above the foot, and the foot is shaded for the cost.
        self.assertLess(sum(icon.getpixel((54, 100))[:3]), 120, "the foot is clear of the subject and dark")
        self.assertGreater(sum(icon.getpixel((54, 70))[:3]), 500, "the subject stands above it")
        with self.assertRaises(ValueError):
            ui.icon_frame(subject, trim="hue")

    def test_command_icon_frame_fills_the_button(self):
        from godot_agent.assets import ui
        subject = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        ImageDraw.Draw(subject).rectangle((52, 40, 76, 88), fill=(220, 40, 40, 255))
        icon = ui.icon_frame(subject, trim="bronze", background="#3d6cb4", edge="#0a1226", size=64)
        self.assertEqual(icon.size, (64, 64))
        r, g, b, a = icon.getpixel((32, 34))
        self.assertGreater(r, 150, "the subject is scaled up to fill the button")
        self.assertLess(sum(icon.getpixel((0, 32))[:3]), 60, "a dark outline round the bevel")
        self.assertGreater(sum(icon.getpixel((1, 20))[:3]), sum(icon.getpixel((62, 44))[:3]),
                           "the bevel is lit at the top left")


    def test_terrain_texture_is_seamless_and_mipmapped(self):
        result = pipeline.make_texture(str(self.project), "grass", "res://textures/grass.png", size=64,
                                       import_into_godot=False)
        self.assertTrue(result["ok"], result["problems"])
        self.assertIn("mipmaps/generate=true", (self.project / "textures" / "grass.png.import").read_text())
        with Image.open(self.project / "textures" / "grass.png") as img:
            px = img.convert("RGB").load()
            size = img.width

            def column_gap(a: int, b: int) -> float:
                return sum(sum(abs(px[a, y][c] - px[b, y][c]) for c in range(3)) for y in range(size)) / size

            # Across the wrap the image changes no more than between any two neighbouring columns.
            inner = sum(column_gap(x, x + 1) for x in range(size - 1)) / (size - 1)
            self.assertLess(column_gap(size - 1, 0), inner * 1.6)

    def test_foliage_cards_are_alpha_cut_with_a_card_material(self):
        result = pipeline.make_texture(str(self.project), "foliage_grass", "res://textures/foliage_grass.png",
                                       size=64, import_into_godot=False)
        self.assertTrue(result["ok"], result["problems"])
        with Image.open(self.project / "textures" / "foliage_grass.png") as img:
            alphas = img.convert("RGBA").getchannel("A").tobytes()
        # Drawn with an alpha cut at 0.42 in mind: clear around the plant, solid where it stands.
        self.assertGreater(alphas.count(0), len(alphas) // 5, "the card is mostly cut out")
        self.assertGreater(sum(a > 107 for a in alphas), len(alphas) // 20, "and has solid blades")
        tres = (self.project / "materials" / "foliage_grass.tres").read_text()
        self.assertIn("res://materials/foliage.gdshader", tres)
        self.assertIn("res://textures/foliage_grass.png", tres)

    @unittest.skipUnless(shutil.which("blender"), "blender not installed")
    def test_blender_model_keeps_rig_animation_and_material_names(self):
        pipeline.make_texture(str(self.project), "foliage_grass", "res://textures/foliage_grass.png", size=64,
                              import_into_godot=False)
        script = self.tmp / "post.py"
        script.write_text(BLENDER_SCRIPT)
        result = pipeline.make_blender(str(self.project), str(script), "res://models/post.glb", seed=2)
        self.assertTrue(result["ok"], result["problems"])
        from godot_agent.assets import meshes
        info = meshes.analyze_model(self.project / "models" / "post.glb")
        self.assertEqual(info["animations"], ["walk"])
        self.assertEqual(sorted(info["material_names"]), ["foliage_grass", "painted"])
        mapping = (self.project / "models" / "post.glb.import").read_text()
        self.assertIn("res://materials/painted.tres", mapping)
        self.assertIn("res://materials/foliage_grass.tres", mapping)
        recipe = json.loads((self.project / "asset_manifest.json").read_text())["assets"]["models/post.glb"]
        self.assertEqual((recipe["tool"], recipe["seed"]), ("blender", 2))


if __name__ == "__main__":
    unittest.main()
