"""Tests for token-spy security and API key generation."""

import os
import secrets
import stat
import tempfile
from pathlib import Path


class TestTokenSpyApiKey:

    def test_generated_key_written_atomically_with_restricted_permissions(self, tmp_path):
        key_file = tmp_path / "token-spy-api-key.txt"
        key = secrets.token_urlsafe(32)
        fd, tmp_str = tempfile.mkstemp(dir=str(tmp_path), prefix=".token-spy-api-key.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
        tmp_obj = Path(tmp_str)
        tmp_obj.chmod(0o600)
        os.replace(tmp_obj, key_file)
        assert key_file.exists()
        assert key_file.read_text(encoding="utf-8") == key
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
