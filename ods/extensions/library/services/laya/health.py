import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3) as response:
    value = json.load(response)
if value != {'status': 'ok', 'startupInferenceVerified': True}:
    raise SystemExit('Laya startup inference has not been verified')
