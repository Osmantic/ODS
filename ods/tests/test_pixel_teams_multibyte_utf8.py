"""Verify SSE chunk decoding handles multibyte UTF-8 sequences split across chunks."""
import codecs
import unittest

class PixelTeamsMultibyteUtf8Tests(unittest.TestCase):
    def test_incremental_decoder_handles_split_multibyte_sequence(self):
        full_char = "🚀"
        char_bytes = full_char.encode("utf-8")  # 4 bytes: b'\xf0\x9f\x9a\x80'
        chunk1 = char_bytes[:2]
        chunk2 = char_bytes[2:]

        decoder = codecs.getincrementaldecoder("utf-8")()
        decoded1 = decoder.decode(chunk1)
        self.assertEqual(decoded1, "")  # buffered internally

        decoded2 = decoder.decode(chunk2)
        self.assertEqual(decoded2, full_char)

    def test_direct_decode_fails_on_split_chunk(self):
        chunk1 = "🚀".encode("utf-8")[:2]
        with self.assertRaises(UnicodeDecodeError):
            chunk1.decode("utf-8")

if __name__ == "__main__":
    unittest.main()
