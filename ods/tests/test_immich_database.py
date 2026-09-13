"""Check the rendered server/client contract and real PostgreSQL authentication."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "extensions/library/services/immich/compose.yaml"
KEYS = ("IMMICH_DB_USER", "IMMICH_DB_NAME", "IMMICH_DB_PASSWORD")


def run(*args, env=None):
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=150)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def environment(values):
    env = {key: value for key, value in os.environ.items() if key not in KEYS}
    return {**env, **values}


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI required")
@pytest.mark.parametrize("values,expected", [
    ({}, ("postgres", "immich", "postgres")),
    ({"IMMICH_DB_USER": "photo owner", "IMMICH_DB_NAME": "photo archive", "IMMICH_DB_PASSWORD": "fixture-punctuation-$!'"},
     ("photo owner", "photo archive", "fixture-punctuation-$!'")),
    (dict.fromkeys(KEYS, ""), ("postgres", "immich", "postgres")),
])
def test_rendered_database_identity_matches_application(tmp_path, values, expected):
    plan = json.loads(run("docker", "compose", "--project-directory", str(tmp_path), "-f", str(COMPOSE),
                          "config", "--format", "json", env=environment(values)))
    client = plan["services"]["immich"]["environment"]
    server = plan["services"]["immich-postgres"]["environment"]
    # Compose escapes literal dollars when serializing a reusable config plan.
    assert (client["DB_USERNAME"], client["DB_DATABASE_NAME"], client["DB_PASSWORD"].replace("$$", "$")) == expected
    assert (server["POSTGRES_USER"], server["POSTGRES_DB"], server["POSTGRES_PASSWORD"].replace("$$", "$")) == expected


@pytest.mark.skipif(os.environ.get("ODS_TEST_IMMICH_POSTGRES") != "1", reason="Opt-in shipped PostgreSQL image")
def test_application_credentials_authenticate_and_survive_recreation(tmp_path):
    name = "ods-immich-db-test-" + uuid.uuid4().hex[:10]
    env = environment({"IMMICH_DB_USER": "photo owner", "IMMICH_DB_NAME": "photo archive",
                       "IMMICH_DB_PASSWORD": "fixture-$!'" + uuid.uuid4().hex,
                       "IMMICH_DB_HOST": "immich-postgres"})
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  immich-postgres:
    container_name: {name}
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(COMPOSE), "-f", str(overlay)]
    plan = json.loads(run(*command, "config", "--format", "json", env=env))
    client = plan["services"]["immich"]["environment"]
    # Use the application's network address: initdb may trust localhost clients.
    login = ["docker", "exec", "-e", "PGPASSWORD=" + client["DB_PASSWORD"].replace("$$", "$"), name, "psql", "--host=" + client["DB_HOSTNAME"],
             "--username=" + client["DB_USERNAME"], "--dbname=" + client["DB_DATABASE_NAME"], "-At", "-v", "ON_ERROR_STOP=1"]
    run("docker", "network", "create", name)
    try:
        started = subprocess.run([*command, "up", "-d", "--wait", "--wait-timeout", "90", "immich-postgres"],
                                 capture_output=True, text=True, timeout=150, env=env)
        assert started.returncode == 0, started.stderr + "\n" + run("docker", "logs", name)
        identity = json.loads(run(*login, "-c", "SELECT json_build_array(current_user, current_database())"))
        assert identity == ["photo owner", "photo archive"]
        wrong_login = list(login)
        wrong_login[3] = "PGPASSWORD=deliberately-wrong-fixture"
        denied = subprocess.run([*wrong_login, "-c", "SELECT 1"], capture_output=True, text=True, timeout=20)
        assert denied.returncode != 0
        assert "password authentication failed" in denied.stderr
        run(*login, "-c", "CREATE TABLE ods_receipt (value text); INSERT INTO ods_receipt VALUES ('retained');")
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90", "immich-postgres", env=env)
        assert run(*login, "-c", "SELECT value FROM ods_receipt") == "retained"
    finally:
        run(*command, "down", "--timeout", "10", env=env)
        run("docker", "network", "rm", name)
