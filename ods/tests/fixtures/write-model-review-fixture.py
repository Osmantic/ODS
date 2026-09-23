#!/usr/bin/env python3
"""Prepare synthetic source/ack records in an isolated installer test directory."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
target = Path(sys.argv[1]).resolve()
sha256 = sys.argv[2]
sys.path.insert(0, str(ROOT / "extensions/services/dashboard-api"))
from model_terms import hub_source_observation, project_terms  # noqa: E402

revision = "a" * 40
model = {"id": "test-full", "name": "Synthetic test model", "source_repo": "fixture/Model",
         "source_revision": revision, "gguf_file": "Full.gguf", "gguf_sha256": sha256,
         "gguf_url": "https://huggingface.co/fixture/Model/resolve/" + revision + "/Full.gguf"}
model["terms"] = hub_source_observation(model)
(target / "config").mkdir(parents=True, exist_ok=True)
(target / "config/model-library.json").write_text(json.dumps({"models": [model]}), encoding="utf-8")
for relative in ("scripts/review-model-download.py", "extensions/services/dashboard-api/model_terms.py",
                 "installers/lib/model-download-review.sh"):
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / relative, path)
spec = importlib.util.spec_from_file_location("fixture_review", ROOT / "scripts/review-model-download.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
review.write_receipt(target / "data/model-download-review.json", model,
                     {"termsDigest": project_terms(model)["termsDigest"], "acknowledged": True})
