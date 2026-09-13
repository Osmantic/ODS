"""Serve a generated ZIM through the installed recipe's real public HTTP API."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/kiwix"


def test_kiwix_catalog_matches_the_installed_listener(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"), "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "kiwix")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    assert entry == next(item for item in checked["extensions"] if item["id"] == "kiwix")
    assert (entry["port"], entry["external_port_default"]) == (8080, 8105)


@pytest.mark.skipif(os.environ.get("ODS_TEST_KIWIX_LIVE") != "1", reason="Opt-in Docker/ZIM HTTP test")
def test_local_archive_articles_search_and_recreation(tmp_path):
    import httpx
    from libzim.writer import Creator, Hint, Item, StringProvider

    class Article(Item):
        def get_path(self):
            return "home"

        def get_title(self):
            return "ODS nebula field guide"

        def get_mimetype(self):
            return "text/html"

        def get_contentprovider(self):
            return StringProvider('<html><head><meta charset="utf-8"><title>ODS nebula field guide</title></head>'
                                  '<body><h1>ODS nebula field guide</h1><p>Local nebula knowledge — Xin chào.</p></body></html>')

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: True}

    name = "ods-kiwix-test-" + uuid.uuid4().hex[:10]
    data = tmp_path / "data/kiwix"
    data.mkdir(parents=True)
    archive = data / "field guide.zim"
    with Creator(str(archive)).config_indexing(True, "eng") as creator:
        creator.set_mainpath("home")
        creator.add_item(Article())
        for key, value in {"Name": "ods-fixture", "Title": "ODS Offline Fixture", "Language": "eng",
                           "Description": "Locally generated regression archive", "Creator": "ODS tests",
                           "Publisher": "ODS tests", "Date": "2026-09-14"}.items():
            creator.add_metadata(key, value)
    before = archive.read_bytes()
    archive.chmod(0o644)
    data.chmod(0o755)
    installed = tmp_path / "extensions/services/kiwix"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  kiwix:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    env = {**os.environ, "KIWIX_ZIM_FILE": archive.name}

    def run(*args):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    missing = subprocess.run([*command, "config"], env={**env, "KIWIX_ZIM_FILE": ""}, capture_output=True, text=True)
    assert missing.returncode != 0 and "Set KIWIX_ZIM_FILE" in missing.stderr
    run("docker", "network", "create", name)
    try:
        for _ in range(2):
            run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
            base = "http://" + run(*command, "port", "kiwix", "8080")
            with httpx.Client(base_url=base, timeout=10, follow_redirects=True) as client:
                catalog = client.get("/catalog/v2/entries")
                catalog.raise_for_status()
                feed = ET.fromstring(catalog.content)
                assert "ODS Offline Fixture" in [node.text for node in feed.iter("{http://www.w3.org/2005/Atom}title")]
                article = client.get("/content/field_guide/home")
                article.raise_for_status()
                assert "Local nebula knowledge — Xin chào." in article.text
                search = client.get("/search", params={"books.name": "field_guide", "pattern": "nebula"})
                search.raise_for_status()
                assert "ODS nebula field guide" in search.text
                missing_article = client.get("/content/field_guide/not-present")
                assert missing_article.status_code == 404
            run(*command, "down", "--timeout", "10")
        assert archive.read_bytes() == before
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
