from __future__ import annotations

import copy
import hashlib
import json

import pytest

import extension_configuration as configuration


def contract(
    key: str,
    *,
    config_type: str = "string",
    required: bool = True,
    secret: bool = False,
    source: str = "user",
    validation: dict | None = None,
    default: object | None = None,
) -> dict:
    result = {
        "key": key,
        "type": config_type,
        "required": required,
        "secret": secret,
        "source": source,
        "restartBehavior": "service",
    }
    if validation is not None:
        result["validation"] = validation
    if default is not None:
        result["default"] = default
    return result


def envelope(*definitions: tuple[str, list[dict]]) -> dict:
    required_config = sorted(
        {
            item["key"]
            for _, items in definitions
            for item in items
            if item["required"] and not item["secret"]
        }
    )
    required_secrets = sorted(
        {
            item["key"]
            for _, items in definitions
            for item in items
            if item["required"] and item["secret"]
        }
    )
    plan = {
        "schema": "ods.assistant-first.plan.v1",
        "selectedServices": [service_id for service_id, _ in definitions],
        "definitions": [
            {"id": service_id, "configuration": copy.deepcopy(items)}
            for service_id, items in definitions
        ],
        "requiredConfigKeys": required_config,
        "requiredSecretKeys": required_secrets,
    }
    serialized = json.dumps(
        plan,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema": "ods.assistant-first.plan-envelope.v1",
        "planHash": hashlib.sha256((serialized + "\n").encode()).hexdigest(),
        "plan": plan,
    }


def error(code: str, call) -> configuration.ExtensionConfigurationError:
    with pytest.raises(configuration.ExtensionConfigurationError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def contracts(stored: dict) -> dict[str, dict]:
    return configuration.configuration_contracts(
        stored, expected_plan_hash=stored["planHash"]
    )


def schema(stored: dict) -> dict:
    return configuration.configuration_schema(
        stored, expected_plan_hash=stored["planHash"]
    )


def submit(stored: dict, values: dict, secrets: dict) -> dict:
    return configuration.validate_configuration_submission(
        stored,
        values,
        secrets,
        expected_plan_hash=stored["planHash"],
    )


def test_contracts_are_derived_only_from_stored_selected_services() -> None:
    stored = envelope(("notes", [contract("NOTES_PATH")]))
    assert contracts(stored) == {"NOTES_PATH": contract("NOTES_PATH")}


def test_plan_hash_is_verified_before_contract_extraction() -> None:
    stored = envelope(("notes", [contract("NOTES_PATH")]))
    stored["plan"]["definitions"][0]["configuration"] = []
    error("plan-hash-mismatch", lambda: contracts(stored))


def test_caller_must_supply_the_external_stored_plan_hash_anchor() -> None:
    stored = envelope(("notes", [contract("NOTES_PATH")]))
    error(
        "stored-plan-hash-mismatch",
        lambda: configuration.configuration_contracts(
            stored, expected_plan_hash="0" * 64
        ),
    )


@pytest.mark.parametrize(
    ("unsafe_value", "code"),
    [
        (1.5, "float-not-canonical"),
        ("x" * 65_537, "invalid-configuration-text"),
    ],
    ids=["float", "oversized-string"],
)
def test_plan_canonicalization_rejects_unsafe_json_shapes(
    unsafe_value, code: str
) -> None:
    stored = envelope(("notes", []))
    stored["plan"]["unsafe"] = unsafe_value
    rehash(stored)
    error(code, lambda: contracts(stored))


def test_plan_canonicalization_rejects_cycles() -> None:
    stored = envelope(("notes", []))
    stored["plan"]["cycle"] = stored["plan"]
    error("configuration-input-too-large", lambda: contracts(stored))


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda plan: plan["definitions"].append(
                {"id": "extra", "configuration": []}
            ),
            "selected-definition-mismatch",
        ),
        (
            lambda plan: plan["definitions"].append(
                copy.deepcopy(plan["definitions"][0])
            ),
            "duplicate-service-definition",
        ),
        (
            lambda plan: plan["definitions"].clear(),
            "selected-definition-mismatch",
        ),
        (
            lambda plan: plan["selectedServices"].append("notes"),
            "duplicate-selected-service",
        ),
    ],
)
def test_selected_services_and_definitions_are_one_to_one(mutation, code: str) -> None:
    stored = envelope(("notes", []))
    mutation(stored["plan"])
    stored = rehash(stored)
    error(code, lambda: contracts(stored))


def rehash(stored: dict) -> dict:
    plan = stored["plan"]
    serialized = json.dumps(
        plan,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    stored["planHash"] = hashlib.sha256((serialized + "\n").encode()).hexdigest()
    return stored


def test_identical_shared_contract_is_deduplicated() -> None:
    shared = contract("SHARED")
    stored = envelope(("one", [shared]), ("two", [shared]))
    assert list(contracts(stored)) == ["SHARED"]


def test_conflicting_shared_contract_is_rejected() -> None:
    stored = envelope(
        ("one", [contract("SHARED")]),
        ("two", [contract("SHARED", required=False)]),
    )
    error(
        "configuration-contract-conflict",
        lambda: contracts(stored),
    )


def test_required_key_summaries_must_match_derived_contracts() -> None:
    stored = envelope(("notes", [contract("NOTES_PATH")]))
    stored["plan"]["requiredConfigKeys"] = []
    rehash(stored)
    error(
        "required-configuration-contract-mismatch",
        lambda: contracts(stored),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.update(extra="unexpected"),
        lambda item: item.update(key="lowercase"),
        lambda item: item.update(source="caller"),
        lambda item: item.update(restartBehavior="host"),
    ],
)
def test_contract_metadata_is_exact_and_strict(mutation) -> None:
    item = contract("VALUE")
    mutation(item)
    stored = envelope(("notes", [item]))
    error(
        "invalid-configuration-contract-fields"
        if "extra" in item
        else "invalid-configuration-key"
        if item["key"] == "lowercase"
        else "invalid-configuration-contract",
        lambda: contracts(stored),
    )


def test_secret_schema_keeps_metadata_but_never_default_or_value() -> None:
    stored = envelope(
        (
            "provider",
            [
                contract(
                    "API_TOKEN",
                    secret=True,
                    validation={"minLength": 16, "maxLength": 64},
                )
            ],
        )
    )
    field = schema(stored)["fields"][0]
    assert field == {
        "key": "API_TOKEN",
        "type": "string",
        "required": True,
        "secret": True,
        "source": "user",
        "restartBehavior": "service",
        "validation": {"minLength": 16, "maxLength": 64},
    }
    assert "default" not in field
    assert "value" not in field


def test_schema_hash_binds_secret_contract_metadata() -> None:
    first = envelope(("provider", [contract("TOKEN", secret=True)]))
    second = envelope(("provider", [contract("TOKEN", secret=True, required=False)]))
    assert schema(first)["schemaHash"] != schema(second)["schemaHash"]


@pytest.mark.parametrize(
    "validation",
    [
        "not-an-object",
        {},
        {"pattern": "^safe$"},
        {"minimum": 1},
        {"minLength": True},
        {"minLength": 5, "maxLength": 4},
    ],
)
def test_string_validation_is_structured_bounded_and_non_executable(validation) -> None:
    stored = envelope(("notes", [contract("NAME", validation=validation)]))
    error(
        "invalid-configuration-object"
        if isinstance(validation, str)
        else "invalid-validation-fields"
        if not validation or "pattern" in validation
        else "incompatible-validation"
        if "minimum" in validation
        else "invalid-validation-bound"
        if validation.get("minLength") is True
        else "invalid-validation-range",
        lambda: contracts(stored),
    )


@pytest.mark.parametrize(
    "validation",
    [None, {"choices": []}, {"choices": ["one", "one"]}, {"maxLength": 4}],
)
def test_enum_requires_unique_nonempty_choices(validation) -> None:
    stored = envelope(
        ("provider", [contract("MODE", config_type="enum", validation=validation)])
    )
    expected = {
        "None": "missing-enum-validation",
        "{'choices': []}": "invalid-enum-choices",
        "{'choices': ['one', 'one']}": "duplicate-enum-choice",
        "{'maxLength': 4}": "incompatible-validation",
    }[str(validation)]
    error(expected, lambda: contracts(stored))


def test_secret_default_is_rejected_without_echoing_it() -> None:
    sentinel = "do-not-echo-private-token"
    stored = envelope(("provider", [contract("TOKEN", secret=True, default=sentinel)]))
    caught = error(
        "secret-default-forbidden",
        lambda: contracts(stored),
    )
    assert sentinel not in str(caught)
    assert sentinel not in repr(caught)
    assert sentinel not in json.dumps(caught.as_dict())


def test_default_must_satisfy_its_declared_validation() -> None:
    stored = envelope(
        (
            "notes",
            [
                contract(
                    "WORKERS",
                    config_type="integer",
                    validation={"minimum": 2, "maximum": 8},
                    default=1,
                )
            ],
        )
    )
    error(
        "configuration-value-too-small",
        lambda: contracts(stored),
    )


def test_required_nonsecret_default_satisfies_an_omitted_submission() -> None:
    stored = envelope(
        ("notes", [contract("WORKERS", config_type="integer", default=4)])
    )
    receipt = submit(stored, {}, {})
    assert receipt["appliedDefaultKeys"] == ["WORKERS"]
    assert receipt["presentConfigKeys"] == []


def test_zero_default_and_optional_subset_are_preserved_as_presence_metadata() -> None:
    stored = envelope(
        (
            "notes",
            [
                contract("COUNT", config_type="integer", default=0),
                contract("LABEL", required=False),
            ],
        )
    )
    receipt = submit(stored, {"LABEL": "chosen"}, {})
    assert receipt["appliedDefaultKeys"] == ["COUNT"]
    assert receipt["presentConfigKeys"] == ["LABEL"]


def test_non_user_required_field_is_not_a_user_submission_requirement() -> None:
    stored = envelope(
        (
            "notes",
            [contract("GENERATED", source="generated")],
        )
    )
    assert submit(stored, {}, {})["presentConfigKeys"] == []


def test_required_user_config_and_secret_are_reported_by_key_only() -> None:
    stored = envelope(
        (
            "provider",
            [contract("ENDPOINT"), contract("TOKEN", secret=True)],
        )
    )
    caught = error(
        "missing-required-configuration",
        lambda: submit(stored, {}, {}),
    )
    assert caught.as_dict()["details"] == {
        "configKeys": ["ENDPOINT"],
        "secretKeys": ["TOKEN"],
    }


def test_submission_enforces_secret_split_and_source() -> None:
    stored = envelope(
        (
            "provider",
            [
                contract("TOKEN", secret=True),
                contract("GENERATED", source="generated", required=False),
            ],
        )
    )
    error(
        "secret-in-nonsecret-values",
        lambda: submit(stored, {"TOKEN": "private"}, {}),
    )
    error(
        "configuration-source-restricted",
        lambda: submit(
            stored, {"GENERATED": "caller-controlled"}, {"TOKEN": "private"}
        ),
    )


def test_secret_value_is_validated_but_absent_from_receipt_and_errors() -> None:
    sentinel = "short-private-value"
    stored = envelope(
        (
            "provider",
            [
                contract(
                    "TOKEN",
                    secret=True,
                    validation={"minLength": 32},
                )
            ],
        )
    )
    caught = error(
        "configuration-value-too-short",
        lambda: submit(stored, {}, {"TOKEN": sentinel}),
    )
    assert sentinel not in str(caught)
    assert sentinel not in repr(caught)
    assert sentinel not in json.dumps(caught.as_dict())


def test_valid_submission_receipt_contains_presence_only() -> None:
    sentinel = "private-value-1234567890"
    stored = envelope(
        (
            "provider",
            [
                contract("ENDPOINT", config_type="url"),
                contract("TOKEN", secret=True, validation={"minLength": 16}),
            ],
        )
    )
    receipt = submit(
        stored,
        {"ENDPOINT": "https://example.invalid/v1"},
        {"TOKEN": sentinel},
    )
    assert receipt["presentConfigKeys"] == ["ENDPOINT"]
    assert receipt["presentSecretKeys"] == ["TOKEN"]
    assert sentinel not in json.dumps(receipt)


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.invalid",
        "https://",
        "https://user:password@example.invalid",
        "https://example.invalid/path#fragment",
        "https://example.invalid:99999",
        "https://exa mple.invalid",
        "https://example.invalid/%0aheader",
        "https://еxample.invalid",
    ],
)
def test_url_validation_is_absolute_credentialless_and_bounded(value: str) -> None:
    stored = envelope(("provider", [contract("ENDPOINT", config_type="url")]))
    error(
        "invalid-configuration-url",
        lambda: submit(stored, {"ENDPOINT": value}, {}),
    )


def test_http_is_allowed_for_explicit_local_routes() -> None:
    stored = envelope(("provider", [contract("ENDPOINT", config_type="url")]))
    receipt = submit(stored, {"ENDPOINT": "http://127.0.0.1:11434/v1"}, {})
    assert receipt["presentConfigKeys"] == ["ENDPOINT"]


def test_integer_rejects_boolean_and_enforces_bounds() -> None:
    stored = envelope(
        (
            "notes",
            [
                contract(
                    "WORKERS",
                    config_type="integer",
                    validation={"minimum": 1, "maximum": 8},
                )
            ],
        )
    )
    error(
        "invalid-configuration-value-type",
        lambda: submit(stored, {"WORKERS": True}, {}),
    )
    error(
        "configuration-value-too-large",
        lambda: submit(stored, {"WORKERS": 9}, {}),
    )


def test_enum_submission_must_match_a_declared_choice() -> None:
    stored = envelope(
        (
            "provider",
            [
                contract(
                    "MODE",
                    config_type="enum",
                    validation={"choices": ["local", "external"]},
                )
            ],
        )
    )
    error(
        "invalid-configuration-choice",
        lambda: submit(stored, {"MODE": "other"}, {}),
    )


def test_unknown_and_duplicate_submission_keys_fail_closed() -> None:
    stored = envelope(("notes", [contract("PATH", required=False)]))
    error(
        "unknown-configuration-key",
        lambda: submit(stored, {"UNKNOWN": "value"}, {}),
    )
    error(
        "duplicate-submission-key",
        lambda: submit(stored, {"PATH": "one"}, {"PATH": "two"}),
    )
