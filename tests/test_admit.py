"""Memory admission: every Godot process the kit starts waits for room first.

    python3 -m unittest tests/test_admit.py -v
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from godot_agent import env, runner  # noqa: E402


class AdmitTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(env, "CACHE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plenty_of_memory_starts_at_once(self):
        with mock.patch.object(env, "mem_available_gb", return_value=64.0):
            self.assertEqual(env.admit(), 0.0)

    def test_unknown_memory_starts_at_once(self):
        with mock.patch.object(env, "mem_available_gb", return_value=None):
            self.assertEqual(env.admit(), 0.0)

    def test_short_of_memory_waits_until_room(self):
        readings = iter([1.0, 1.0, 1.0, 1.0, 20.0])

        def reading():
            return next(readings, 20.0)

        with mock.patch.object(env, "mem_available_gb", side_effect=reading), \
                mock.patch.object(env, "ADMIT_SPACING_S", 0.0):
            waited = env.admit(timeout=30)
        self.assertGreaterEqual(waited, 2.0, "it waited for the memory to come back")
        self.assertLess(waited, 10.0)

    def test_gives_up_waiting_after_the_timeout(self):
        with mock.patch.object(env, "mem_available_gb", return_value=0.5), \
                mock.patch.object(env, "ADMIT_SPACING_S", 0.0):
            started = time.monotonic()
            env.admit(timeout=1.5)
        self.assertLess(time.monotonic() - started, 5.0, "a guard, never a deadlock")


class RunCappedTest(unittest.TestCase):
    def test_a_process_that_keeps_allocating_is_stopped(self):
        grow = "import time\nheap = []\nwhile True:\n    heap.append(bytearray(8 << 20))\n    time.sleep(0.01)\n"
        with mock.patch.object(runner, "MAX_RSS_GB", 0.3):
            started = time.monotonic()
            code, _, reason = runner.run_capped([sys.executable, "-c", grow], timeout=60)
        self.assertEqual(code, -1)
        self.assertIn("over the 0.3 GB cap", reason)
        self.assertLess(time.monotonic() - started, 30)

    def test_a_process_past_its_timeout_is_stopped(self):
        code, _, reason = runner.run_capped([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertEqual((code, reason), (-1, "timed out after 1s"))

    def test_a_process_that_ends_keeps_its_code_and_output(self):
        code, output, reason = runner.run_capped([sys.executable, "-c", "print('hello'); raise SystemExit(3)"],
                                                 timeout=30)
        self.assertEqual((code, output.strip(), reason), (3, b"hello", None))


if __name__ == "__main__":
    unittest.main()
