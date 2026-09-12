import pytest
from unittest.mock import MagicMock

def test_url_normalization():
    def normalize(url, path):
        url = url.rstrip("/")
        path = (path or "/v1").strip("/")
        return f"{url}/{path}/chat/completions".replace("/v1/v1", "/v1")

    # Case 1: Both have /v1
    assert normalize("http://localhost:11434/v1", "/v1") == "http://localhost:11434/v1/chat/completions"
    # Case 2: URL has /v1, path is empty
    assert normalize("http://localhost:11434/v1", "") == "http://localhost:11434/v1/chat/completions"
    # Case 3: Neither has /v1 (should use default v1)
    assert normalize("http://localhost:11434", "") == "http://localhost:11434/v1/chat/completions"
    # Case 4: URL has no /v1, path has /v1
    assert normalize("http://localhost:11434", "/v1") == "http://localhost:11434/v1/chat/completions"

if __name__ == '__main__':
    test_url_normalization()
    print('SUCCESS: URL normalization verified')
