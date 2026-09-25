import json
import os
import sys
import urllib.request

try:
    request = urllib.request.Request("http://127.0.0.1:9099/v1/models", headers={"Authorization": "Bearer " + os.environ["PIPELINES_API_KEY"]})
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read(1024 * 1024))
        valid = response.status == 200 and isinstance(payload.get("data"), list) and payload.get("pipelines") is True
    sys.exit(0 if valid else 1)
except Exception:
    sys.exit(1)
