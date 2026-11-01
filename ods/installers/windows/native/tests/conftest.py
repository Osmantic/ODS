"""Put ods/installers/windows/native on sys.path so tests import the `supervisor` package
the same way the bundled runtime does (`python -m supervisor`)."""
import sys
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parents[1]
if str(NATIVE_DIR) not in sys.path:
    sys.path.insert(0, str(NATIVE_DIR))
