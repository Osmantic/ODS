import sys
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard import reconciler as rc


def test_switchboard_reconciler_import():
    assert rc is not None
