"""Asset pipeline self-test against a temporary copy of the fixture project.

    python3 -m unittest tests/test_assets.py -v

Needs Godot and Pillow; the Aseprite test is skipped when aseprite is missing.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from PIL import Image, ImageDraw  # noqa: E402

from godot_agent.assets import pipeline  # noqa: E402

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
    ],
}

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


if __name__ == "__main__":
    unittest.main()
