"""Unit test suite for URL query parameter dictionary encoding resilience."""

import unittest
from urllib.parse import urlencode


def encode_query_parameters_safely(params_dict: dict | None) -> str:
    if not params_dict or not isinstance(params_dict, dict):
        return ""
    cleaned = {}
    for k, v in params_dict.items():
        if k and isinstance(k, str):
            key_str = str(k).strip()
            if key_str and v is not None:
                cleaned[key_str] = str(v)
    return urlencode(cleaned)


class TestQueryParamEncoderResilience(unittest.TestCase):
    def test_encode_query_params_valid(self):
        query = encode_query_parameters_safely({"search": "llama 3", "page": 1})
        self.assertEqual(query, "search=llama+3&page=1")

    def test_encode_query_params_none(self):
        self.assertEqual(encode_query_parameters_safely(None), "")


if __name__ == "__main__":
    unittest.main()
