"""Tick-exact fast-forward: the ticks_per_frame hook, its step, and the time_scale warning.

    python3 -m unittest tests/test_ticks.py -v

Needs Godot; runs the fixture headless.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
FIXTURE = KIT / "tests" / "fixture"
sys.path.insert(0, str(KIT))

from godot_agent import runner  # noqa: E402
from godot_agent.env import GodotAgentError  # noqa: E402


class TicksPerFrameTest(unittest.TestCase):
    def run_fixture(self, *, paths=None, inline=None, **kwargs):
        out = tempfile.mkdtemp(prefix="ga-ticks-")
        self.addCleanup(shutil.rmtree, out, True)
        return runner.run_scenarios(FIXTURE, paths=paths, inline=inline, headless=True, out=out, **kwargs)

    def test_game_reads_ticks_per_frame_before_its_scene_and_after_a_step(self):
        summary = self.run_fixture(paths=[str(FIXTURE / "scenarios" / "ticks_per_frame.json")])
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))
        self.assertEqual(record["assertions"], 4)
        # 8 ticks a frame before set_ticks_per_frame: the counter moved 80 in 10 frames.
        self.assertEqual(record["vars"]["t1"] - record["vars"]["t0"], 80)

    def test_default_is_one_tick_per_frame(self):
        summary = self.run_fixture(inline={
            "name": "default_ticks", "scene": "res://ticks.tscn",
            "steps": [{"assert": "scene.at_ready == 1"}, {"eval": "scene.sim.tick", "as": "t0"}, {"wait": 10},
                      {"assert": "scene.sim.tick - t0 == 10"}]})
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"], record.get("reason"))

    def test_bad_values_are_refused_before_launch(self):
        for bad in ({"ticks_per_frame": -1}, {"ticks_per_frame": "8"}, {"ticks_per_frame": True},
                    {"steps": [{"set_ticks_per_frame": 1.5}]}, {"max_fps": -2}):
            with self.subTest(bad=bad), self.assertRaises(GodotAgentError):
                runner.validate_scenario(bad, "test")
        runner.validate_scenario({"ticks_per_frame": 0, "steps": [{"set_ticks_per_frame": 8}]}, "test")

    def test_time_scale_above_one_under_fixed_fps_warns(self):
        self.assertEqual(runner.kit_warnings({"ticks_per_frame": 8}), [])
        self.assertEqual(runner.kit_warnings({"time_scale": 0.5}), [])
        self.assertEqual(runner.kit_warnings({"time_scale": 8}, visible=True), [])
        warning = runner.kit_warnings({"time_scale": 8})
        self.assertEqual(len(warning), 1)
        self.assertIn("ticks_per_frame", warning[0])

        summary = self.run_fixture(inline={"name": "scaled", "time_scale": 4, "steps": [{"wait": 2}]})
        record = summary["scenarios"][0]
        self.assertTrue(record["passed"])
        self.assertIn("time_scale 4", record["notes"][0])
        self.assertIn("note: time_scale 4", runner.format_summary(summary))


if __name__ == "__main__":
    unittest.main()
