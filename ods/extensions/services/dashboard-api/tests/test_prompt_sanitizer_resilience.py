"""Unit test suite for prompt sanitizer resilience."""

import unittest


def sanitize_prompt_input(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()


class TestPromptSanitizerResilience(unittest.TestCase):
    def test_sanitize_prompt_valid_string(self):
        self.assertEqual(sanitize_prompt_input("  hello prompt  "), "hello prompt")

    def test_sanitize_prompt_invalid_input(self):
        self.assertEqual(sanitize_prompt_input(None), "")
        self.assertEqual(sanitize_prompt_input(123), "")


if __name__ == "__main__":
    unittest.main()
