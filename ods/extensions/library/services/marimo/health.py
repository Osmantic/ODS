import sys
import urllib.request

try:
    with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
