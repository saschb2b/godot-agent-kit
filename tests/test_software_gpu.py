"""A rendered run that drew on a software rasterizer is named as such (a timeout there may be
the renderer's, not the game's)."""

import unittest

from godot_agent.runner import software_gpu

LLVMPIPE = ("Godot Engine v4.7.1.stable.official.a13da4feb - https://godotengine.org\n"
            "MESA: info: vulkan: No DRI3 support detected - required for presentation\n"
            "Vulkan 1.4.335 - Forward+ - Using Device #1: Unknown - llvmpipe (LLVM 21.1.8, 256 bits)\n")
GPU = ("Godot Engine v4.7.1.stable.official.a13da4feb - https://godotengine.org\n"
       "Vulkan 1.4.335 - Forward+ - Using Device #0: AMD - AMD Radeon 780M (RADV PHOENIX)\n")


class SoftwareGpuTest(unittest.TestCase):
    def test_llvmpipe_is_named(self):
        self.assertEqual(software_gpu(LLVMPIPE), "llvmpipe")

    def test_a_real_gpu_is_none(self):
        self.assertIsNone(software_gpu(GPU))

    def test_only_the_logs_head_counts(self):
        self.assertIsNone(software_gpu(GPU + "x" * 5000 + "a scenario printing llvmpipe\n"))


if __name__ == "__main__":
    unittest.main()
