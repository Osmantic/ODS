#!/usr/bin/env python3
import subprocess
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "installers/lib/compose-failure-report.sh"

def test_port_line():
    with tempfile.TemporaryDirectory() as td:
        env_file = Path(td) / ".env"
        env_file.write_text("LLAMA_SERVER_PORT=8080\n")
        cmd = f'''
source "{SCRIPT}"
env_file="{env_file}"
llama_port="$(_ods_report_env_value "$env_file" LLAMA_SERVER_PORT "$(_ods_report_env_value "$env_file" LLM_PORT "$(_ods_report_env_value "$env_file" OLLAMA_PORT "11434")")")"
echo "PORT=$llama_port"
'''
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        assert res.returncode == 0
        assert "PORT=8080" in res.stdout
    print("test_compose_failure_report_port_check passed.")

if __name__ == "__main__":
    test_port_line()
