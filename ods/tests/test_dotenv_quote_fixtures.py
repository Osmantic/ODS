import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOTENV_QUOTE_SH = ROOT / "lib/dotenv-quote.sh"

def test_dotenv_quote_exists():
    assert DOTENV_QUOTE_SH.is_file()

def test_dotenv_quote_syntax():
    res = subprocess.run(["bash", "-n", str(DOTENV_QUOTE_SH)], capture_output=True, text=True)
    assert res.returncode == 0
