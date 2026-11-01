"""A scenario's user_files reach the run's private user://, where Godot looks for them."""

import tempfile
import unittest
from pathlib import Path

from godot_agent.env import GodotAgentError, user_dir, write_user_files


def project(text: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="ga-user-files-"))
    (root / "project.godot").write_text(text, encoding="utf-8")
    return root


class UserFilesTest(unittest.TestCase):
    def test_app_userdata_under_the_runs_data_home(self):
        root = project('[application]\nconfig/name="Groundhold"\n')
        self.assertEqual(user_dir(root, "/tmp/d"), Path("/tmp/d/godot/app_userdata/Groundhold"))

    def test_a_custom_user_dir(self):
        root = project('[application]\nconfig/name="Shore: Two"\nconfig/use_custom_user_dir=true\n')
        self.assertEqual(user_dir(root, "/tmp/d"), Path("/tmp/d/Shore_ Two"))
        root = project('[application]\nconfig/name="A"\nconfig/use_custom_user_dir=true\n'
                       'config/custom_user_dir_name="mine"\n')
        self.assertEqual(user_dir(root, "/tmp/d"), Path("/tmp/d/mine"))

    def test_files_are_written_inside_user_only(self):
        root = project('[application]\nconfig/name="Groundhold"\n')
        home = tempfile.mkdtemp(prefix="ga-home-")
        written = write_user_files(root, home, {"net.cfg": "[device]\ntoken=\"ab\"\n",
                                                "user://saves/one.cfg": "x"})
        self.assertEqual([p.read_text() for p in written], ["[device]\ntoken=\"ab\"\n", "x"])
        self.assertEqual(written[1], Path(home) / "godot/app_userdata/Groundhold/saves/one.cfg")
        for bad in ("../escape.cfg", "/etc/passwd", ""):
            with self.assertRaises(GodotAgentError):
                write_user_files(root, home, {bad: "x"})
        self.assertEqual(write_user_files(root, home, None), [])


if __name__ == "__main__":
    unittest.main()
