"""The determinism lint behind `godot-agent check <project> --sim-lint <folder>`.

    python3 -m unittest tests/test_sim_lint.py -v

The last test needs Godot: it runs the whole check on a copy of the fixture.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
FIXTURE = KIT / "tests" / "fixture"
sys.path.insert(0, str(KIT))

from godot_agent import simlint  # noqa: E402
from godot_agent.check import check_project, format_check  # noqa: E402


def found(source: str) -> list[tuple[int, str, str]]:
    problems, _ = simlint.lint_source(source)
    return [(p["line"], p["rule"], p["token"]) for p in problems]


class SimLintTest(unittest.TestCase):
    def test_every_forbidden_token_of_the_contract(self):
        cases = {
            "var a: float = 0": ("float", "float"),
            "var a := 1.5": ("float-literal", "1.5"),
            "var a := .5": ("float-literal", ".5"),
            "var a := 2.": ("float-literal", "2."),
            "var a := 1e3": ("float-literal", "1e3"),
            "var a := 1_000.25": ("float-literal", "1_000.25"),
            "var a := sin(x)": ("sin", "sin"),
            "var a := cos (x)": ("cos", "cos"),
            "var a := tan(x)": ("tan", "tan"),
            "var a := atan2(y, x)": ("atan2", "atan2"),
            "var a := sqrt(x)": ("sqrt", "sqrt"),
            "var a := pow(x, 2)": ("pow", "pow"),
            "var a := exp(x)": ("exp", "exp"),
            "var a := log(x)": ("log", "log"),
            "var a := lerp(a, b, t)": ("lerp", "lerp"),
            "var a := lerp_angle(a, b, t)": ("lerp", "lerp_angle"),
            "var a := lerpf(a, b, t)": ("lerp", "lerpf"),
            "var a := randf()": ("randf", "randf"),
            "var a := randi() % 3": ("randi", "randi"),
            "var a := randfn(0, 1)": ("randf", "randfn"),
            "var a := rng.randi_range(0, 3)": ("randi", "randi_range"),
            "ids.shuffle()": ("shuffle", "shuffle"),
            "var a = ids.pick_random()": ("pick_random", "pick_random"),
            "var a := Vector2(1, 2)": ("Vector2", "Vector2"),
            "var a: Vector3": ("Vector3", "Vector3"),
            "var a := Basis()": ("Basis", "Basis"),
            "var a: Transform3D": ("Transform", "Transform3D"),
            "var a: Transform2D": ("Transform", "Transform2D"),
            "look_at(target)": ("look_at", "look_at"),
            "var a := get_tree()": ("get_tree", "get_tree"),
            "var a := tree.get_nodes_in_group(&\"creeps\")": ("get_nodes_in_group", "get_nodes_in_group"),
            "var a := Time.get_ticks_msec()": ("Time.", "Time."),
            "var a := OS.get_ticks_usec()": ("OS.get_ticks", "OS.get_ticks_usec"),
            "func step(delta: int) -> void:": ("delta", "delta"),
            "Engine.time_scale = 2": ("time_scale", "time_scale"),
            "var a := hash(state)": ("hash(", "hash"),
            "extends Node": ("node-type", "Node"),
            "var a: Node3D": ("node-type", "Node3D"),
            "var a := Timer.new()": ("node-type", "Timer"),
        }
        for source, (rule, token) in cases.items():
            with self.subTest(source=source):
                self.assertIn((1, rule, token), found(source))

    def test_lookalikes_pass(self):
        clean = "\n".join([
            "extends RefCounted",
            "var cell := Vector2i(3, 4) + Vector3i(1, 2, 3).x",
            "var big := 0x1e5 + 0b101 + 1_000_000 + 10",
            "var delta_um := next_dist_um - dist_um",
            "var h := state_hash(serialize())",
            "var t := turns.log(4)  # a method named log, not the global log()",
            "var integer := int(7) / 2 % 3",
            "func is_float_free() -> bool: return true",
            "var range_sq := range_mm * range_mm",
        ])
        self.assertEqual(found(clean), [])

    def test_strings_and_comments_are_not_code(self):
        source = "\n".join([
            "## Uses sqrt(2.0) and Vector2 in a doc comment",
            "var a := \"Time.get_ticks_msec() 0.5 Vector3\"  # randf() in a comment",
            "var b := '''sqrt(4.0)'''",
            "var c := \"\"\"multi",
            "line delta 1.5",
            "string\"\"\" + str(sqrt(9))",
            "var d := &\"lerp\" + ^\"Node2D\" + \"escaped \\\" quote 1.5\"",
        ])
        # Only the call after the multi-line string counts, on the line it is on.
        self.assertEqual(found(source), [(6, "sqrt", "sqrt")])

    def test_allow_comment_needs_a_reason(self):
        source = "\n".join([
            "var a := sqrt(4)  # sim-lint: allow sqrt only in this one-off setup table",
            "var b := pow(2, 3)  # sim-lint: allow pow",
            "var c := 0.5 + sin(x)  # sim-lint: allow 0.5,sin both fed only to the view",
            "var d := 0.25  # sim-lint: allow float-literal display scale for the debug view",
            "var e := Vector2(1, 1)  # sim-lint: allow Vector3 the wrong token does not excuse Vector2",
        ])
        problems, excused = simlint.lint_source(source)
        self.assertEqual([(p["line"], p["rule"], p["token"]) for p in problems],
                         [(2, "allow", "pow"), (2, "pow", "pow"), (5, "Vector2", "Vector2")])
        self.assertEqual(excused, 4)

    def test_report_names_file_line_and_column(self):
        # The project itself may sit under a hidden folder (a worktree in .claude/worktrees/);
        # only hidden folders below the linted one are skipped.
        with tempfile.TemporaryDirectory() as root:
            tmp = str(Path(root) / ".worktrees" / "game")
            sim = Path(tmp) / "scripts" / "sim"
            sim.mkdir(parents=True)
            (sim / ".backup").mkdir()
            (sim / ".backup" / "old.gd").write_text("var speed := 1.5\n")
            (sim / "ok.gd").write_text("extends RefCounted\nvar tick := 0\n")
            (sim / "nested").mkdir()
            (sim / "nested" / "bad.gd").write_text("extends RefCounted\n\nvar speed := 1.5\n")
            report = simlint.lint_paths(tmp, ["scripts/sim"])
            self.assertEqual(report["files"], 2)
            self.assertEqual(len(report["problems"]), 1)
            problem = report["problems"][0]
            self.assertEqual((problem["file"], problem["line"], problem["column"], problem["token"]),
                             (str(Path("scripts/sim/nested/bad.gd")), 3, 14, "1.5"))
            self.assertEqual(simlint.lint_paths(tmp, ["res://scripts/sim/ok.gd"])["problems"], [])
            missing = simlint.lint_paths(tmp, ["scripts/nope"])
            self.assertEqual(missing["problems"][0]["rule"], "path")

    def test_check_fails_on_a_forbidden_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "fixture"
            shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns(".godot"))
            clean = check_project(project, sim_lint=["sim"])
            self.assertTrue(clean["ok"], format_check(clean))
            self.assertEqual(clean["sim_lint"]["files"], 1)
            # Compiles fine, so only the lint can fail it.
            (project / "sim" / "drift.gd").write_text(
                "extends RefCounted\n\nfunc speed(x: int) -> int:\n\treturn int(sqrt(x))\n")
            dirty = check_project(project, sim_lint=["sim"])
            self.assertFalse(dirty["ok"])
            self.assertEqual(dirty["errors"], [])
            self.assertEqual([(p["file"], p["line"], p["rule"]) for p in dirty["sim_lint"]["problems"]],
                             [(str(Path("sim/drift.gd")), 4, "sqrt")])
            self.assertIn("sim/drift.gd:4:13  sqrt", format_check(dirty))
            cli = subprocess.run([sys.executable, str(KIT / "bin" / "godot-agent"), "check", str(project),
                                  "--sim-lint", "sim", "--no-refresh"], capture_output=True, text=True)
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
            self.assertIn("1 forbidden tokens", cli.stdout)


if __name__ == "__main__":
    unittest.main()
