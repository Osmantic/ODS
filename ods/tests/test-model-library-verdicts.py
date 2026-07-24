"""app_compatibility verdict lifecycle contract.

Fleet-recorded compatibility verdicts block models from release planning
(see test-model-library-coverage.py BLOCKING_AGENT_STATUSES), so a verdict
recorded by a since-fixed probe or product era can silently make the
six-distinct-model release matrix unsatisfiable. Every new or updated
blocking verdict must therefore carry lifecycle metadata that says when it
was recorded, against what, where it applies, and what invalidates it:

  recordedAt            ISO-8601 UTC timestamp of the recording run
  productSha            product commit the verdict was recorded against
  harnessSha            harness commit that recorded it
  hostScope             non-empty list of fleet host names it applies to, or
  globalScope           true when the verdict intentionally applies globally
  revalidateAgainstSha  product commit at/after which the verdict must be
                        re-earned before it may keep blocking
  expiresAt             (alternative trigger) ISO-8601 UTC expiry

Verdicts predating this contract are grandfathered behind a ratchet: their
count may only shrink (migrate entries as they are touched or revalidated).
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "model-library.json"

BLOCKING_STATUSES = {
    "blocked",
    "incompatible",
    "not_agent_viable",
    "not_recommended",
    "not_supported",
    "unsupported",
    "unsupported_until_revalidated",
}

LIFECYCLE_REQUIRED = {"recordedAt", "productSha", "harnessSha"}
REVALIDATION_TRIGGERS = {"revalidateAgainstSha", "expiresAt"}

# Blocking verdicts recorded before the lifecycle contract existed.
# This number may only decrease; never add a new blocking verdict without
# the lifecycle fields.
LEGACY_UNMIGRATED_RATCHET = 36

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{12,40}$")


def _verdict_rows():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for model in catalog["models"]:
        compatibility = model.get("app_compatibility") or {}
        for app, verdict in compatibility.items():
            if isinstance(verdict, dict):
                yield model.get("id", "?"), app, verdict


def _is_blocking(verdict):
    return str(verdict.get("status") or "").strip().lower() in BLOCKING_STATUSES


def _has_lifecycle(verdict):
    has_scope = "hostScope" in verdict or verdict.get("globalScope") is True
    return LIFECYCLE_REQUIRED <= set(verdict) and has_scope


def test_lifecycle_bearing_verdicts_are_well_formed():
    checked = 0
    for model_id, app, verdict in _verdict_rows():
        if not _has_lifecycle(verdict):
            continue
        checked += 1
        where = f"{model_id}.app_compatibility.{app}"
        assert _ISO_RE.match(str(verdict["recordedAt"])), \
            f"{where}: recordedAt must be ISO-8601 UTC"
        assert _SHA_RE.match(str(verdict["productSha"])), \
            f"{where}: productSha must be 12-40 hex chars"
        assert _SHA_RE.match(str(verdict["harnessSha"])), \
            f"{where}: harnessSha must be 12-40 hex chars"
        if verdict.get("globalScope") is True:
            assert "hostScope" not in verdict, (
                f"{where}: use either globalScope or hostScope, not both"
            )
        else:
            hosts = verdict["hostScope"]
            assert isinstance(hosts, list) and hosts and all(
                isinstance(h, str) and h for h in hosts
            ), f"{where}: hostScope must be a non-empty list of host names"
        if _is_blocking(verdict):
            triggers = REVALIDATION_TRIGGERS & set(verdict)
            assert len(triggers) == 1, (
                f"{where}: a blocking verdict needs exactly one of "
                f"{sorted(REVALIDATION_TRIGGERS)}"
            )
            trigger = triggers.pop()
            value = str(verdict[trigger])
            if trigger == "revalidateAgainstSha":
                assert _SHA_RE.match(value), \
                    f"{where}: revalidateAgainstSha must be 12-40 hex chars"
            else:
                assert _ISO_RE.match(value), \
                    f"{where}: expiresAt must be ISO-8601 UTC"
    assert checked >= 1, "expected at least one lifecycle-bearing verdict"


def test_unmigrated_blocking_verdicts_only_shrink():
    legacy = [
        f"{model_id}.{app}"
        for model_id, app, verdict in _verdict_rows()
        if _is_blocking(verdict) and not _has_lifecycle(verdict)
    ]
    assert len(legacy) <= LEGACY_UNMIGRATED_RATCHET, (
        "New blocking app_compatibility verdicts must carry lifecycle "
        "metadata (recordedAt/productSha/harnessSha and hostScope/globalScope plus a "
        "revalidation trigger). Unmigrated legacy verdicts may only "
        f"shrink from {LEGACY_UNMIGRATED_RATCHET}; found {len(legacy)}: "
        + ", ".join(sorted(legacy))
    )
