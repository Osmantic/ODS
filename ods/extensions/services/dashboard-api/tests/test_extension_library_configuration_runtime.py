from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


BIN = Path(__file__).resolve().parents[4] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_library_configuration_binding as binding  # noqa: E402
import extension_library_configuration_runtime as runtime  # noqa: E402
from extension_lifecycle_plan import PlannedOperation  # noqa: E402
from extension_lifecycle_work import (  # noqa: E402
    LifecycleWorkExecutionError,
    LifecycleWorkValidationError,
)
import test_extension_library_configuration_binding as helpers  # noqa: E402


def _command(definitions, *, state="configuring"):
    original = helpers._command(definitions, state=state)
    service_ids = tuple(item.service_id for item in definitions)
    return replace(
        original,
        operation_key="configure",
        service_ids=service_ids,
        payload={"serviceIds": list(service_ids)},
    )


def _four_service_case():
    definitions = [
        helpers._definition(
            "gitea", [helpers._field("GITEA_PORT", kind="integer", default=7830)]
        ),
        helpers._definition(
            "miniflux",
            [
                helpers._field("MINIFLUX_PORT", kind="integer", default=8098),
                helpers._field("MINIFLUX_DB_PASSWORD", required=True, secret=True),
            ],
        ),
        helpers._definition(
            "ntfy", [helpers._field("NTFY_PORT", kind="integer", default=8097)]
        ),
        helpers._definition(
            "ollama", [helpers._field("OLLAMA_PORT", kind="integer", default=11434)]
        ),
    ]
    record = helpers._record(
        definitions,
        secret_keys=("MINIFLUX_DB_PASSWORD",),
        default_keys=("GITEA_PORT", "MINIFLUX_PORT", "NTFY_PORT", "OLLAMA_PORT"),
        reference=helpers.REFERENCE,
    )
    transaction = helpers._transaction(definitions, record)
    transaction["state"] = "configuring"
    return definitions, record, transaction


def test_library_configuring_proves_all_four_without_materializing_secrets():
    definitions, record, transaction = _four_service_case()
    requests = []
    reads = []
    dispatcher = runtime.build_library_configuration_runtime(
        transaction_loader=lambda transaction_id: (
            reads.append(transaction_id) or transaction
        ),
        secret_status=lambda request: (
            requests.append(request) or helpers._status(record)
        ),
    ).dispatcher
    command = _command(definitions)

    evidence = dispatcher(command)

    assert len(evidence) == 64
    assert evidence == dispatcher(command)
    assert reads == [helpers.TXN, helpers.TXN]
    assert len(requests) == 2
    assert all(request["reference"] == helpers.REFERENCE for request in requests)
    assert "password-value" not in repr(requests)
    assert binding.bind_library_configuration(
        command,
        lambda _id: transaction,
        lambda _request: helpers._status(record),
        expected_state="configuring",
        target_service_id="miniflux",
    ).secret_keys == ("MINIFLUX_DB_PASSWORD",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: replace(command, operation_key="apply:gitea"),
        lambda command: replace(command, payload={"serviceIds": ["gitea"]}),
        lambda command: replace(command, service_ids=("gitea",)),
        lambda command: replace(
            command, service_ids=("gitea", "miniflux", "ntfy", "searxng")
        ),
        lambda command: replace(
            command, plan_material=replace(command.plan_material, state="applying")
        ),
        lambda command: replace(
            command,
            plan_material=replace(command.plan_material, attested_approval=False),
        ),
    ],
)
def test_command_drift_is_denied_before_store_access(mutate):
    definitions, _, _ = _four_service_case()
    calls = []
    dispatcher = runtime.LibraryConfigurationDispatcher(
        lambda _id: calls.append("transaction"),
        lambda _request: calls.append("secret"),
    )

    with pytest.raises(LifecycleWorkValidationError):
        dispatcher(mutate(_command(definitions)))
    assert calls == []


def test_configuration_approval_drift_is_denied():
    definitions, record, transaction = _four_service_case()
    transaction["approval"]["configurationHash"] = "9" * 64
    calls = []
    dispatcher = runtime.LibraryConfigurationDispatcher(
        lambda _id: transaction,
        lambda request: calls.append(request) or helpers._status(record),
    )

    with pytest.raises(
        LifecycleWorkValidationError, match="library-configuration-approval-mismatch"
    ):
        dispatcher(_command(definitions))
    assert calls == []


def test_secret_status_mismatch_is_denied_without_a_secret_value():
    definitions, record, transaction = _four_service_case()
    dispatcher = runtime.LibraryConfigurationDispatcher(
        lambda _id: transaction,
        lambda _request: helpers._status(record, presentSecretKeys=[]),
    )

    with pytest.raises(
        binding.LibraryConfigurationBindingError,
        match="library-configuration-secret-mismatch",
    ) as error:
        dispatcher(_command(definitions))
    assert helpers.REFERENCE not in str(error.value)


def test_library_configuring_cannot_admit_an_unapproved_or_mixed_batch():
    definitions, record, transaction = _four_service_case()
    dispatcher = runtime.LibraryConfigurationDispatcher(
        lambda _id: transaction,
        lambda _request: helpers._status(record),
    )
    for service_id in ("searxng", "documents"):
        changed = list(definitions)
        changed[-1] = helpers._definition(service_id, [])
        with pytest.raises(LifecycleWorkValidationError):
            dispatcher(_command(changed))


def test_noop_library_definition_is_not_in_the_configuring_batch():
    definitions = [
        helpers._definition("gitea", [helpers._field("GITEA_PORT", default="7830")]),
        helpers._definition("ntfy", [helpers._field("NTFY_PORT", default="8097")]),
    ]
    transaction = helpers._transaction(definitions, None)
    transaction["state"] = "configuring"
    command = _command(definitions)
    command = replace(
        command,
        service_ids=("gitea",),
        payload={"serviceIds": ["gitea"]},
        plan_material=replace(
            command.plan_material,
            operations=(
                PlannedOperation("gitea", "install"),
                PlannedOperation("ntfy", "noop"),
            ),
        ),
    )
    # The builder rejects missing custody even for a default-only plan.
    # A callable status provider is still required but is never invoked.
    with pytest.raises(
        LifecycleWorkExecutionError, match="library-configuration-runtime-invalid"
    ):
        runtime.LibraryConfigurationDispatcher(lambda _id: transaction, None)
    dispatcher = runtime.LibraryConfigurationDispatcher(
        lambda _id: transaction,
        lambda _request: (_ for _ in ()).throw(AssertionError("unexpected secret")),
    )
    assert len(dispatcher(command)) == 64
