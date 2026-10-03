#!/usr/bin/env python3
"""Review or apply one exact Pixel-system binding to a new paired-run configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import portal_outcome_evaluation as evaluation
import portal_outcome_pair as pair


BINDING_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-pair-system-binding-v1.schema.json"
BINDING_BOUNDARY = (
    "Trusted-terminal review and destination-bound publication of one new owner-private Pixel/Codex pair "
    "configuration that changes only the selected Pixel system configuration and the unused preflight "
    "destination. It requires the exact successful Pixel policy-binding receipt, does not edit any current "
    "configuration, start a model, service, task, tool, or network, use credentials, perform an external "
    "effect, deploy, or claim completion, publication, acceptance, or promotion authority."
)
SYSTEM_POLICY_BINDING_BOUNDARY = (
    "Trusted-terminal review and destination-bound publication of one new owner-private Pixel comparison "
    "configuration that changes only the policy template pointer to one exact enabled, prepared, currently "
    "qualified DSV4 policy. It does not edit or replace the current configuration, enable or mutate policy, "
    "start a model or service, route a job, use network or credentials, perform an external effect, or grant "
    "completion, publication, deployment, acceptance, or promotion authority."
)
CHANGES = {
    "writesNewPrivateConfiguration": True,
    "changesOnlyPixelSystemAndPreflightPaths": True,
    "requiresSuccessfulPixelPolicyBinding": True,
    "editsCurrentConfiguration": False,
    "startsModel": False,
    "startsService": False,
    "routesJob": False,
    "usesNetwork": False,
    "usesCredentials": False,
    "externalEffects": False,
}
AUTHORITY = {
    "grantsExecution": False,
    "grantsModelStart": False,
    "grantsServiceStart": False,
    "grantsNetwork": False,
    "grantsCredentials": False,
    "grantsExternalEffects": False,
    "grantsCompletion": False,
    "grantsPublication": False,
    "grantsDeployment": False,
    "grantsAcceptance": False,
    "grantsPromotion": False,
}
SYSTEM_RECEIPT_FIELDS = {
    "schemaVersion", "operation", "status", "operationSha256", "proposedConfigurationSha256",
    "enabledPolicySha256", "qualificationReceiptSha256", "enabledProfiles", "comparisonProfiles",
    "destinationName", "changes", "authority", "boundary",
}
SYSTEM_RECEIPT_CHANGES = {
    "writesNewPrivateConfiguration": True,
    "changesOnlyPolicyTemplatePath": True,
    "editsCurrentConfiguration": False,
    "mutatesPolicy": False,
    "startsModel": False,
    "startsService": False,
    "routesJob": False,
    "usesNetwork": False,
    "usesCredentials": False,
    "externalEffects": False,
}
SYSTEM_RECEIPT_AUTHORITY = {
    "grantsExecution": False,
    "grantsModelStart": False,
    "grantsServiceStart": False,
    "grantsNetwork": False,
    "grantsCredentials": False,
    "grantsExternalEffects": False,
    "grantsCompletion": False,
    "grantsPublication": False,
    "grantsDeployment": False,
    "grantsAcceptance": False,
    "grantsPromotion": False,
}


def _absolute(value: Path | str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or path != Path(os.path.abspath(path)):
        raise evaluation.OutcomeError(f"{label} must be an absolute canonical non-root path")
    return path


def _new_destination(value: Path | str, label: str) -> Path:
    path = _absolute(value, label)
    evaluation.private_parent(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    except OSError as exc:
        raise evaluation.OutcomeError(f"{label} is unavailable") from exc
    raise evaluation.OutcomeError(f"{label} must be a new private file")


def _valid_hash(value: Any, label: str) -> str:
    return evaluation.valid_hash(value, label)


def _system_binding_receipt(path: Path, pixel_system_path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = evaluation.read_json(path, "private Pixel system policy-binding receipt", private=True)
    value = evaluation.exact_fields(value, SYSTEM_RECEIPT_FIELDS, "Pixel system policy-binding receipt")
    for field in ("operationSha256", "proposedConfigurationSha256", "enabledPolicySha256", "qualificationReceiptSha256"):
        _valid_hash(value[field], f"Pixel system policy-binding {field}")
    profiles = value["enabledProfiles"]
    comparison_profiles = value["comparisonProfiles"]
    if (
        value["schemaVersion"] != 1
        or value["operation"] != "pixel-portal-outcome-system-policy-binding-apply"
        or value["status"] != "policy-bound-system-configuration-written"
        or not isinstance(profiles, list) or not profiles
        or any(not isinstance(profile, str) for profile in profiles) or len(profiles) != len(set(profiles))
        or any(profile not in {"assistant", "scout", "builder", "data-lab", "researcher"} for profile in profiles)
        or not isinstance(comparison_profiles, list) or not comparison_profiles
        or any(not isinstance(profile, str) for profile in comparison_profiles)
        or any(
            profile not in {"assistant", "builder", "controller", "researcher"}
            or profile != "controller" and profile not in profiles
            or profile == "controller" and "builder" not in profiles
            for profile in comparison_profiles
        )
        or value["destinationName"] != pixel_system_path.name
        or value["changes"] != SYSTEM_RECEIPT_CHANGES
        or value["authority"] != SYSTEM_RECEIPT_AUTHORITY
        or value["boundary"] != SYSTEM_POLICY_BINDING_BOUNDARY
    ):
        raise evaluation.OutcomeError("Pixel system policy-binding receipt is not the exact successful safe transition")
    return value, raw


def _serialized(value: dict[str, Any]) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def _validate_proposed_configuration(value: dict[str, Any], candidate_parent: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pixel-pair-system-review-", dir=candidate_parent) as temporary:
        root = Path(temporary).resolve(strict=True)
        if os.name != "nt":
            root.chmod(0o700)
        path = root / "pair-system.json"
        path.write_bytes(_serialized(value))
        if os.name != "nt":
            path.chmod(0o600)
        pair.load_configuration_record(path)


def _proposal(
    *, configuration_path: Path, pixel_system_path: Path, pixel_binding_receipt_path: Path,
    candidate_path: Path, preflight_path: Path,
) -> dict[str, Any]:
    configuration_path = _absolute(configuration_path, "current pair configuration")
    pixel_system_path = _absolute(pixel_system_path, "new Pixel system configuration")
    pixel_binding_receipt_path = _absolute(pixel_binding_receipt_path, "Pixel system policy-binding receipt")
    candidate_path = _new_destination(candidate_path, "pair configuration destination")
    preflight_path = _new_destination(preflight_path, "pair preflight destination")
    distinct = {str(path).lower() if os.name == "nt" else str(path) for path in (
        configuration_path, pixel_system_path, pixel_binding_receipt_path, candidate_path, preflight_path,
    )}
    if len(distinct) != 5:
        raise evaluation.OutcomeError("pair binding paths must be distinct")
    current, current_raw = pair.load_configuration_record(configuration_path)
    pixel_system, pixel_system_raw = evaluation.read_json(
        pixel_system_path, "private policy-bound Pixel system configuration", private=True,
    )
    binding_receipt, binding_receipt_raw = _system_binding_receipt(pixel_binding_receipt_path, pixel_system_path)
    pixel_system_sha256 = evaluation.sha256(evaluation.canonical(pixel_system))
    if binding_receipt["proposedConfigurationSha256"] != pixel_system_sha256:
        raise evaluation.OutcomeError("Pixel system configuration differs from its successful policy-binding receipt")
    if current["pixelSystemConfigPath"] == str(pixel_system_path):
        raise evaluation.OutcomeError("pair configuration already selects the reviewed Pixel system")
    proposed = dict(current)
    proposed["pixelSystemConfigPath"] = str(pixel_system_path)
    proposed["preflightPath"] = str(preflight_path)
    if any(proposed[field] != current[field] for field in current if field not in {"pixelSystemConfigPath", "preflightPath"}):
        raise evaluation.OutcomeError("pair binding proposal changed an unreviewed field")
    _validate_proposed_configuration(proposed, candidate_path.parent)
    proposed_raw = _serialized(proposed)
    current_sha256 = evaluation.sha256(current_raw)
    pixel_system_file_sha256 = evaluation.sha256(pixel_system_raw)
    binding_receipt_sha256 = evaluation.sha256(binding_receipt_raw)
    proposed_sha256 = evaluation.sha256(proposed_raw)
    operation_sha256 = evaluation.sha256(evaluation.canonical({
        "schemaVersion": 1,
        "operation": "pixel-portal-outcome-pair-system-binding",
        "configurationPath": str(configuration_path),
        "currentConfigurationSha256": current_sha256,
        "pixelSystemConfigurationPath": str(pixel_system_path),
        "pixelSystemConfigurationSha256": pixel_system_file_sha256,
        "pixelSystemPolicyBindingReceiptPath": str(pixel_binding_receipt_path),
        "pixelSystemPolicyBindingReceiptSha256": binding_receipt_sha256,
        "candidatePath": str(candidate_path),
        "preflightPath": str(preflight_path),
        "proposedConfigurationSha256": proposed_sha256,
    }))
    common = {
        "$schema": BINDING_SCHEMA,
        "schemaVersion": 1,
        "operationSha256": operation_sha256,
        "currentConfigurationSha256": current_sha256,
        "pixelSystemConfigurationSha256": pixel_system_file_sha256,
        "pixelSystemPolicyBindingReceiptSha256": binding_receipt_sha256,
        "proposedConfigurationSha256": proposed_sha256,
        "changes": dict(CHANGES),
        "authority": dict(AUTHORITY),
        "boundary": BINDING_BOUNDARY,
    }
    return {
        "configurationPath": configuration_path,
        "pixelSystemPath": pixel_system_path,
        "bindingReceiptPath": pixel_binding_receipt_path,
        "candidatePath": candidate_path,
        "preflightPath": preflight_path,
        "currentRaw": current_raw,
        "pixelSystemRaw": pixel_system_raw,
        "bindingReceiptRaw": binding_receipt_raw,
        "proposedRaw": proposed_raw,
        "common": common,
    }


def review_pair_system_binding(**options: Path) -> dict[str, Any]:
    proposal = _proposal(**options)
    common = proposal["common"]
    return {
        **common,
        "operation": "pixel-portal-outcome-pair-system-binding-review",
        "status": "confirmation-required",
        "confirmation": {"option": "--confirm-operation-sha256", "sha256": common["operationSha256"]},
    }


def apply_pair_system_binding(*, confirmation: str, **options: Path) -> dict[str, Any]:
    _valid_hash(confirmation, "pair binding confirmation")
    proposal = _proposal(**options)
    common = proposal["common"]
    if confirmation != common["operationSha256"]:
        raise evaluation.OutcomeError("confirmation differs from the exact pair binding operation")
    current_now = pair.load_configuration_record(proposal["configurationPath"])[1]
    pixel_now = evaluation.read_bytes(proposal["pixelSystemPath"], limit=evaluation.MAX_JSON_BYTES, private=True)
    receipt_now = evaluation.read_bytes(proposal["bindingReceiptPath"], limit=evaluation.MAX_JSON_BYTES, private=True)
    if (
        current_now != proposal["currentRaw"]
        or pixel_now != proposal["pixelSystemRaw"]
        or receipt_now != proposal["bindingReceiptRaw"]
    ):
        raise evaluation.OutcomeError("pair binding inputs changed after review")
    _new_destination(proposal["preflightPath"], "pair preflight destination")
    evaluation.write_new_private(proposal["candidatePath"], proposal["proposedRaw"])
    written = evaluation.read_bytes(proposal["candidatePath"], limit=evaluation.MAX_JSON_BYTES, private=True)
    if written != proposal["proposedRaw"]:
        raise evaluation.OutcomeError("written pair configuration differs from the exact reviewed bytes")
    return {
        **common,
        "operation": "pixel-portal-outcome-pair-system-binding-apply",
        "status": "pair-system-configuration-written",
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("review", "apply"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pixel-system", type=Path, required=True)
    parser.add_argument("--pixel-binding-receipt", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-operation-sha256")
    args = parser.parse_args()
    if (args.operation == "apply") != (args.confirm_operation_sha256 is not None):
        parser.error("apply requires, and review forbids, --confirm-operation-sha256")
    return args


def main() -> int:
    args = _arguments()
    options = {
        "configuration_path": Path(os.path.abspath(args.config)),
        "pixel_system_path": Path(os.path.abspath(args.pixel_system)),
        "pixel_binding_receipt_path": Path(os.path.abspath(args.pixel_binding_receipt)),
        "candidate_path": Path(os.path.abspath(args.candidate)),
        "preflight_path": Path(os.path.abspath(args.preflight)),
    }
    try:
        output = _new_destination(Path(os.path.abspath(args.output)), "pair binding receipt output")
        all_paths = {*options.values(), output}
        if len({str(path).lower() if os.name == "nt" else str(path) for path in all_paths}) != len(all_paths):
            raise evaluation.OutcomeError("pair binding receipt output must be distinct")
        if args.operation == "review":
            result = review_pair_system_binding(**options)
        else:
            result = apply_pair_system_binding(confirmation=args.confirm_operation_sha256, **options)
        evaluation.write_new_private(output, _serialized(result))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (evaluation.OutcomeError, OSError, UnicodeError) as exc:
        print(f"[pixel] ERROR: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
