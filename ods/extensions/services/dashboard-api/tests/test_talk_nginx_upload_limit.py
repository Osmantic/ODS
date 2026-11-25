"""The dashboard's nginx must pass Talk uploads the API itself accepts."""
from pathlib import Path
import re

from routers import talk

NGINX = Path(__file__).resolve().parents[2] / "dashboard/nginx.conf"
UNITS = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}


def _location(source, marker):
    start = source.index(marker)
    return source[start:source.index("\n    }", start)]


def test_talk_body_limit_covers_the_largest_talk_upload():
    source = NGINX.read_text(encoding="utf-8")
    block = _location(source, "    location ^~ /api/talk/ {")
    match = re.search(r"^\s*client_max_body_size\s+(\d+)([kKmMgG]?);", block, re.MULTILINE)
    assert match, "nginx's 1 MB default would refuse Talk images and voice messages"
    limit = int(match.group(1)) * UNITS[match.group(2).lower()]
    assert limit > max(talk.MAX_AUDIO_BYTES, talk.MAX_IMAGE_BYTES, talk.MAX_DOC_BYTES)
