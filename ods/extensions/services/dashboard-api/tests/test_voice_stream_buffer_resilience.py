"""Unit test suite for voice audio stream chunk buffer bounds resilience."""

import unittest


def validate_audio_chunk_buffer(chunk_bytes: bytes | None, max_chunk_size: int = 1048576) -> tuple[bool, int]:
    if not chunk_bytes or not isinstance(chunk_bytes, bytes):
        return False, 0
    size = len(chunk_bytes)
    if size > max_chunk_size:
        return False, size
    return True, size


class TestVoiceStreamBufferResilience(unittest.TestCase):
    def test_validate_audio_chunk_valid(self):
        ok, sz = validate_audio_chunk_buffer(b"pcm_data_chunk")
        self.assertTrue(ok)
        self.assertEqual(sz, 14)

    def test_validate_audio_chunk_oversized(self):
        ok, sz = validate_audio_chunk_buffer(b"x" * 2000000, max_chunk_size=1000000)
        self.assertFalse(ok)
        self.assertEqual(sz, 2000000)


if __name__ == "__main__":
    unittest.main()
