"""Real password ownership, private recipes, meal planning and API tokens."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/mealie"


def test_mealie_catalog_exposes_recipe_planning(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "mealie")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (9000, 7835, "/api/app/about")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "mealie")


@pytest.mark.skipif(os.environ.get("ODS_TEST_MEALIE_DOCKER") != "1",
                    reason="Opt-in exact-image recipe, mealplan and token lifecycle")
def test_private_recipe_and_mealplan_survive_recreation(tmp_path):
    name = "ods-mealie-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/mealie"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/mealie"
    data.mkdir(parents=True)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  mealie:
    container_name: {name}
    ports: !override ["127.0.0.1:0:9000"]
networks:
  ods-network:
    name: {name}
''')
    compose = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 and "up" in args:
            print(result.stderr, flush=True)
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=60)
            print(logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    plan = json.loads(run(*compose, "config", "--format", "json"))["services"]["mealie"]
    assert plan["user"] == "1000:1000" and plan["read_only"] is True
    assert plan["ports"][0]["host_ip"] == "127.0.0.1"
    assert plan["environment"]["ALLOW_SIGNUP"] == "false"
    run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "-v", str(data) + ":/fixture", plan["image"], "-R", "1000:1000", "/fixture")
    run("docker", "network", "create", name)
    password = secrets.token_hex(20)
    try:
        for iteration in range(2):
            run(*compose, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
            origin = "http://" + run(*compose, "port", "mealie", "9000")
            with httpx.Client(base_url=origin + "/api", timeout=30) as client:
                about = client.get("/app/about")
                assert about.status_code == 200, about.text
                assert about.json()["version"] == "v3.26.0"
                assert client.get("/users/self").status_code == 401

                def login(value):
                    response = client.post("/auth/token", data={"username": "changeme@example.com", "password": value})
                    # Every subsequent request must prove its own bearer auth.
                    client.cookies.clear()
                    return response

                if not iteration:
                    initial = login("MyPassword")
                    assert initial.status_code == 200, initial.text
                    initial_headers = {"Authorization": "Bearer " + initial.json()["access_token"]}
                    changed = client.put("/users/password", headers=initial_headers,
                        json={"currentPassword": "MyPassword", "newPassword": password})
                    assert changed.status_code == 200, changed.text
                assert login("MyPassword").status_code == 401
                owner = login(password)
                assert owner.status_code == 200, owner.text
                headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
                if not iteration:
                    token = client.post("/users/api-tokens", headers=headers, json={"name": "ODS recipe workflow"})
                    assert token.status_code == 201, token.text
                    token_id = token.json()["id"]
                    api_headers = {"Authorization": "Bearer " + token.json()["token"]}
                    created = client.post("/recipes", headers=api_headers, json={"name": "Canh rau ODS"})
                    assert created.status_code == 201, created.text
                    slug = created.json()
                    recipe = client.get("/recipes/" + slug, headers=api_headers).json()
                    recipe["description"] = "Công thức riêng của gia đình"
                    recipe["recipeIngredient"] = [{"note": "200 g rau xanh"}]
                    recipe["recipeInstructions"] = [{"text": "Rửa rau, nấu chín và dùng nóng."}]
                    assert recipe["settings"]["public"] is False
                    updated = client.put("/recipes/" + slug, headers=api_headers, json=recipe)
                    assert updated.status_code == 200, updated.text
                    recipe_id = updated.json()["id"]
                    scheduled = client.post("/households/mealplans", headers=api_headers,
                        json={"date": "2030-01-15", "entryType": "dinner", "recipeId": recipe_id})
                    assert scheduled.status_code == 201, scheduled.text
                    meal_id = scheduled.json()["id"]
                details = client.get("/recipes/" + slug, headers=api_headers)
                assert details.status_code == 200, details.text
                assert details.json()["description"] == "Công thức riêng của gia đình"
                assert details.json()["recipeIngredient"][0]["note"] == "200 g rau xanh"
                assert details.json()["recipeInstructions"][0]["text"] == "Rửa rau, nấu chín và dùng nóng."
                assert client.get("/recipes/" + slug).status_code == 401
                meals = client.get("/households/mealplans", headers=api_headers,
                    params={"start_date": "2030-01-15", "end_date": "2030-01-15"})
                assert meals.status_code == 200, meals.text
                assert [(entry["id"], entry["recipeId"]) for entry in meals.json()["items"]] == [(meal_id, recipe_id)]
                if iteration:
                    revoked = client.delete("/users/api-tokens/" + str(token_id), headers=headers)
                    assert revoked.status_code == 200, revoked.text
                    assert client.get("/users/self", headers=api_headers).status_code == 401
    finally:
        run(*compose, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
