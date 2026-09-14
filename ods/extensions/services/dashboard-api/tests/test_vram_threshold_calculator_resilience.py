"""Unit test suite for GPU VRAM allocation threshold calculation resilience."""

import unittest


def is_vram_sufficient(required_mb: int | float, total_mb: int | float, free_mb: int | float, safety_margin: float = 0.1) -> bool:
    if not isinstance(required_mb, (int, float)) or required_mb <= 0:
        return False
    if not isinstance(total_mb, (int, float)) or not isinstance(free_mb, (int, float)):
        return False
    if free_mb <= 0 or total_mb <= 0:
        return False
    available_mb = float(free_mb) * (1.0 - max(0.0, min(0.5, safety_margin)))
    return float(required_mb) <= available_mb


class TestVRAMThresholdCalculatorResilience(unittest.TestCase):
    def test_is_vram_sufficient_valid(self):
        self.assertTrue(is_vram_sufficient(4000, 24000, 16000))

    def test_is_vram_sufficient_insufficient(self):
        self.assertFalse(is_vram_sufficient(20000, 24000, 4000))

    def test_is_vram_sufficient_invalid_input(self):
        self.assertFalse(is_vram_sufficient(-100, 24000, 16000))
        self.assertFalse(is_vram_sufficient(4000, 0, 0))


if __name__ == "__main__":
    unittest.main()
