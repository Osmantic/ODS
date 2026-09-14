"""Unit test suite for chat message payload parsing resilience."""

import unittest


def parse_chat_message_payload(data: dict) -> dict[str, str]:
    if not isinstance(data, dict):
        return {"role": "user", "content": ""}
    role = data.get("role", "user")
    content = data.get("content", "")
    return {
        "role": str(role).strip() if role else "user",
        "content": str(content).strip() if content else "",
    }


class TestChatMessageResilience(unittest.TestCase):
    def test_parse_chat_message_valid(self):
        msg = parse_chat_message_payload({"role": "assistant", "content": "  hello  "})
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"], "hello")

    def test_parse_chat_message_invalid_type(self):
        msg = parse_chat_message_payload(None)
        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"], "")


if __name__ == "__main__":
    unittest.main()
