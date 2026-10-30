"""Rendered runs share machine-wide slots.

    python3 -m unittest tests/test_render_slots.py -v
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from godot_agent import runner  # noqa: E402


class RenderSlotTest(unittest.TestCase):
    def test_one_slot_runs_renders_one_at_a_time(self):
        os.environ["GODOT_AGENT_RENDER_SLOTS"] = "1"
        self.addCleanup(os.environ.pop, "GODOT_AGENT_RENDER_SLOTS")
        # Private lock files: the real slots are machine-wide, and another agent's render
        # holding slot 0 would stall this test.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(runner, "CACHE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        active, peak, lock = [0], [0], threading.Lock()

        def render():
            with runner.render_slot():
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.3)
                with lock:
                    active[0] -= 1

        threads = [threading.Thread(target=render) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(peak[0], 1)

    def test_slot_count_follows_memory(self):
        self.assertGreaterEqual(runner.render_slots(), 1)


if __name__ == "__main__":
    unittest.main()
