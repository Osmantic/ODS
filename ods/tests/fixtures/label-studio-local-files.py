"""Run the shipped Label Studio API against synthetic inside/outside datasets."""

import os
from pathlib import Path
import sys

sys.path.insert(0, "/label-studio/label_studio")
# Suppress unrelated upstream update/error-reporting work in this offline probe.
# Production filesystem settings still come unchanged from the rendered profile.
os.environ["LABEL_STUDIO_LATEST_VERSION_CHECK"] = "false"
os.environ["LABEL_STUDIO_SENTRY_DSN"] = ""
os.environ["LABEL_STUDIO_FRONTEND_SENTRY_DSN"] = ""
# This is an API permission test, not a database durability test.
os.environ["LABEL_STUDIO_DATABASE_NAME"] = ":memory:"

import django

django.setup()

from django.conf import settings
from django.core.management import call_command
from organizations.models import Organization
from rest_framework.test import APIClient
from users.models import User

call_command("migrate", interactive=False, verbosity=0)
user = User.objects.create(email="ods-fixture@example.invalid")
org = Organization.create_organization(created_by=user, title="ODS fixture")
user.active_organization = org
user.save()
client = APIClient()
client.force_authenticate(user=user)
project = client.post("/api/projects/", {"title": "ODS local files fixture"}, format="json")
assert project.status_code == 201, project.content
project_id = project.json()["id"]

inside = Path("/label-studio/upload/fixture-dataset")
outside = Path("/tmp/ods-outside-dataset")
outside.mkdir()
(outside / "outside.txt").write_text("synthetic outside-root marker\n")

def create_storage(path):
    return client.post("/api/storages/localfiles/", {
        "project": project_id, "path": str(path), "use_blob_urls": True,
        "title": path.name,
    }, format="json")

allowed = create_storage(inside)
assert allowed.status_code == 201, allowed.content
relative = os.path.relpath(inside / "sample.txt", settings.LOCAL_FILES_DOCUMENT_ROOT)
download = client.get("/data/local-files/", {"d": relative})
assert download.status_code == 200, download.content
assert b"".join(download.streaming_content) == b"synthetic selected dataset\n"

rejected = create_storage(outside)
relative = os.path.relpath(outside / "outside.txt", settings.LOCAL_FILES_DOCUMENT_ROOT)
outside_download = client.get("/data/local-files/", {"d": relative})
print("Local root:", settings.LOCAL_FILES_DOCUMENT_ROOT)
print("Selected storage/download:", allowed.status_code, download.status_code)
print("Outside storage/download:", rejected.status_code, outside_download.status_code)
if outside_download.status_code == 200:
    assert b"".join(outside_download.streaming_content) == b"synthetic outside-root marker\n"
    print("Outside-root synthetic marker was served")
assert rejected.status_code == 400, "An authenticated project user must not register an outside-root dataset"
assert outside_download.status_code in {400, 403, 404}
