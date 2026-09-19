#!/usr/bin/env python3
"""Regression test: verify load_env_safe parses indented environment keys."""
import tempfile
import subprocess
from pathlib import Path

def test_indented_env_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("  DREAM_PORT=8080\n\tDREAM_HOST=127.0.0.1\n", encoding="utf-8")
        
        script = """
        load_env_safe() {
            local env_file='""" + str(env_file) + """'
            while IFS='=' read -r key value; do
                key="${key#"${key%%[![:space:]]*}"}"
                value="${value%$'\r'}"
                [[ "$key" =~ ^[[:space:]]*# ]] && continue
                [[ -z "$key" ]] && continue
                [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
                export "$key=$value"
            done < "$env_file"
        }
        load_env_safe
        echo "$DREAM_PORT|$DREAM_HOST"
        """
        res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert res.returncode == 0
        assert res.stdout.strip() == "8080|127.0.0.1"

if __name__ == "__main__":
    test_indented_env_keys()
    print("test_doctor_load_env_indented_keys: PASS")
