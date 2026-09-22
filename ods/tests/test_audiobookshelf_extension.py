"""Real local media scanning, protected byte-range playback and saved progress."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/audiobookshelf"


def test_audiobookshelf_catalog_exposes_audio_library(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "audiobookshelf")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (8080, 7833, "/healthcheck")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "audiobookshelf")


@pytest.mark.skipif(os.environ.get("ODS_TEST_AUDIOBOOKSHELF_DOCKER") != "1",
                    reason="Opt-in exact-image audio scanning, streaming and listening progress")
def test_protected_audio_and_progress_survive_recreation(tmp_path):
    name = "ods-audio-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/audiobookshelf"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/audiobookshelf"
    recording = data / "audiobooks/ODS/Tài liệu"
    for directory in (data / "config", data / "metadata", recording):
        directory.mkdir(parents=True)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  audiobookshelf:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    compose = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=15)
            print(result.stderr + logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    plan = json.loads(run(*compose, "config", "--format", "json"))["services"]["audiobookshelf"]
    assert plan["user"] == "1000:1000" and plan["read_only"] is True
    assert next(mount for mount in plan["volumes"] if mount["target"] == "/audiobooks")["read_only"] is True
    assert plan["ports"][0]["host_ip"] == "127.0.0.1"
    # Mirror ODS container_uid preparation, then generate a tiny test recording
    # with the exact image's encoder. No external media or metadata is needed.
    run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "-v", str(data) + ":/fixture", plan["image"], "-R", "1000:1000", "/fixture")
    run("docker", "run", "--rm", "--user", "1000:1000", "--entrypoint", "ffmpeg",
        "-v", str(recording) + ":/fixture", plan["image"], "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "8",
        "-metadata", "album=Tài liệu", "-metadata", "artist=ODS", "-c:a", "libmp3lame", "-q:a", "5",
        "/fixture/recording.mp3")
    original = (recording / "recording.mp3").read_bytes()
    assert len(original) > 1024
    run("docker", "network", "create", name)
    password = secrets.token_hex(20)
    try:
        for iteration in range(2):
            run(*compose, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            origin = "http://" + run(*compose, "port", "audiobookshelf", "8080")
            with httpx.Client(base_url=origin, timeout=30) as client:
                status = client.get("/status")
                assert status.status_code == 200 and status.json()["serverVersion"] == "2.36.0"
                assert status.json()["isInit"] is bool(iteration)
                if not iteration:
                    initialized = client.post("/init", json={"newRoot": {"username": "reader-admin", "password": password}})
                    assert initialized.status_code == 200, initialized.text
                assert client.get("/api/libraries").status_code == 401
                assert client.post("/login", json={"username": "reader-admin", "password": "wrong"}).status_code == 401
                login = client.post("/login", json={"username": "reader-admin", "password": password},
                                    headers={"x-return-tokens": "true"})
                assert login.status_code == 200, login.text
                headers = {"Authorization": "Bearer " + login.json()["user"]["accessToken"]}
                if not iteration:
                    library = client.post("/api/libraries", headers=headers, json={"name": "Local audio",
                                          "mediaType": "book", "folders": [{"fullPath": "/audiobooks"}]})
                    assert library.status_code == 200, library.text
                    library_id = library.json()["id"]
                    scan = client.post(f"/api/libraries/{library_id}/scan", headers=headers)
                    assert scan.status_code == 200, scan.text
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        listing = client.get(f"/api/libraries/{library_id}/items", headers=headers)
                        assert listing.status_code == 200, listing.text
                        items = listing.json()["results"]
                        if items:
                            break
                        time.sleep(1)
                    assert len(items) == 1
                    item_id = items[0]["id"]
                details = client.get("/api/items/" + item_id, headers=headers, params={"expanded": "1"})
                assert details.status_code == 200, details.text
                assert details.json()["media"]["metadata"]["title"] == "Tài liệu"
                playback = client.post(f"/api/items/{item_id}/play", headers=headers,
                                       json={"forceDirectPlay": True, "supportedMimeTypes": ["audio/mpeg"],
                                             "mediaPlayer": "ODS fixture"})
                assert playback.status_code == 200, playback.text
                session = playback.json()
                assert len(session["audioTracks"]) == 1
                track = session["audioTracks"][0]
                assert 7.5 < track["duration"] < 9
                assert track["contentUrl"].startswith("/")
                assert httpx.get(origin + track["contentUrl"]).status_code == 401
                part = client.get(track["contentUrl"], headers={**headers, "Range": "bytes=64-127"})
                assert part.status_code == 206, part.text
                assert part.content == original[64:128]
                assert part.headers["content-range"] == f"bytes 64-127/{len(original)}"
                closed = client.post(f"/api/session/{session['id']}/close", headers=headers, json={})
                assert closed.status_code == 200, closed.text
                if not iteration:
                    saved = client.patch(f"/api/me/progress/{item_id}", headers=headers,
                                         json={"progress": 0.25, "currentTime": 2, "duration": 8, "isFinished": False})
                    assert saved.status_code == 200, saved.text
                progress = client.get(f"/api/me/progress/{item_id}", headers=headers)
                assert progress.status_code == 200, progress.text
                assert progress.json()["currentTime"] == pytest.approx(2)
                assert progress.json()["isFinished"] is False
        assert (recording / "recording.mp3").read_bytes() == original
    finally:
        run(*compose, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
