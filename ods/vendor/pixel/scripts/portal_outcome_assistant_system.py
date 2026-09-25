#!/usr/bin/env python3
"""Execute one real Pixel portal Assistant turn through a fresh accounted local-model proxy.

The caller prepares an isolated OpenClaw home, workspace, onboarding record, exact proxy
configuration, and already-running loopback model backend. This module owns the transient
proxy process and the real ``ControlState.chat_turn`` call, reconciles fresh-before and
clean-after inference receipts, and always tears the proxy down. It performs no grading.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Any, Callable, Iterable

import portal_outcome_assistant_evidence as assistant_evidence
import portal_outcome_assistant_runtime as assistant_runtime
import portal_outcome_evaluation as evaluation


MAX_PROXY_CONFIG_BYTES = 256 * 1024
PROXY_READY_SECONDS = 30
PROXY_STOP_SECONDS = 5


def _exact_absolute(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or path == Path(path.anchor):
        raise evaluation.OutcomeError(f"{label} is not an exact absolute path")
    return path


def _within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_proxy_config(path: Path) -> dict[str, Any]:
    value, _payload = assistant_runtime._private_json(path, "Assistant model proxy config", MAX_PROXY_CONFIG_BYTES)
    qualification = value.get("qualification")
    if (
        value.get("listenHost") != "127.0.0.1" or type(value.get("listenPort")) is not int
        or not 1 <= value["listenPort"] <= 65535 or value.get("allowedClientIpv4") != "127.0.0.1"
        or not isinstance(qualification, dict) or qualification.get("profile") != "assistant"
        or value.get("provider") != "vllm" or not isinstance(value.get("inference"), dict)
    ):
        raise evaluation.OutcomeError("Assistant model proxy is not exact DSV4 loopback configuration")
    return value


def _health(port: int, job_id: str, claim_id: str) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/healthz", headers={"accept": "application/json"})
        response = connection.getresponse()
        payload = response.read(4097)
        if response.status != 200 or len(payload) > 4096:
            return False
        value = json.loads(payload.decode("utf-8", errors="strict"))
        return value == {"status": "ready", "jobId": job_id, "claimId": claim_id}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


def _wait_ready(process: subprocess.Popen[bytes], receipt_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + PROXY_READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise evaluation.OutcomeError("Assistant model proxy exited before readiness")
        if receipt_path.exists() and _health(config["listenPort"], config["jobId"], config["claimId"]):
            value, _payload = assistant_runtime._private_json(
                receipt_path, "initial Assistant model proxy receipt", MAX_PROXY_CONFIG_BYTES,
            )
            return value
        time.sleep(0.05)
    raise evaluation.OutcomeError("Assistant model proxy did not become ready inside its safety deadline")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=PROXY_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=PROXY_STOP_SECONDS)
    else:
        process.communicate(timeout=PROXY_STOP_SECONDS)


def _listener_absent(port: int) -> bool:
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=0.25)
    except OSError:
        return True
    connection.close()
    return False


def execute_assistant_turn(
    *, root: Path, run_root: Path, onboarding_path: Path, proxy_config_path: Path,
    proxy_receipt_path: Path, node_binary: Path, proxy_launcher_path: Path,
    request_payload: bytes, request_sha256: str, expected_provider: str, expected_model: str,
    chat_environment: dict[str, str] | None = None, action_journal_roots: Iterable[Path] = (),
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    control_runner: Callable[..., tuple[int, bytes]] | None = None,
) -> dict[str, Any]:
    """Run and reconcile one disposable Assistant turn; return a private evidence envelope."""
    root = _exact_absolute(root, "Pixel source root")
    run_root = assistant_runtime._private_directory(_exact_absolute(run_root, "Assistant run root"), "Assistant run root")
    onboarding_path = _exact_absolute(onboarding_path, "Assistant onboarding path")
    proxy_config_path = _exact_absolute(proxy_config_path, "Assistant proxy config path")
    proxy_receipt_path = _exact_absolute(proxy_receipt_path, "Assistant proxy receipt path")
    node_binary = _exact_absolute(node_binary, "Node executable")
    proxy_launcher_path = _exact_absolute(proxy_launcher_path, "Assistant proxy launcher")
    if not _within(run_root, onboarding_path) or not _within(run_root, proxy_receipt_path):
        raise evaluation.OutcomeError("Assistant mutable runtime paths escape the disposable run root")
    if proxy_receipt_path.exists():
        raise evaluation.OutcomeError("Assistant model proxy receipt destination is not fresh")
    if not isinstance(request_payload, bytes) or evaluation.sha256(request_payload) != request_sha256:
        raise evaluation.OutcomeError("Assistant system request binding is invalid")
    if not isinstance(expected_provider, str) or not expected_provider or not isinstance(expected_model, str) or not expected_model:
        raise evaluation.OutcomeError("Assistant expected model identity is invalid")
    config = _read_proxy_config(proxy_config_path)
    if config.get("modelId") != expected_model:
        raise evaluation.OutcomeError("Assistant proxy model differs from the admitted model")
    if not node_binary.is_file() or not proxy_launcher_path.is_file():
        raise evaluation.OutcomeError("Assistant proxy launcher runtime is unavailable")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proxy_environment = {
        "PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "NODE_ENV": "production",
    }
    for name in ("SystemRoot", "WINDIR"):
        if name in os.environ:
            proxy_environment[name] = os.environ[name]
    process = process_factory(
        [str(node_binary), str(proxy_launcher_path), "--config", str(proxy_config_path), "--receipt", str(proxy_receipt_path)],
        cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=proxy_environment,
        shell=False, creationflags=creation_flags,
    )
    initial: dict[str, Any] | None = None
    try:
        initial = _wait_ready(process, proxy_receipt_path, config)
        control = assistant_evidence._load_control(root)
        control_state = control.ControlState(
            root, run_root / "control-state", onboarding_path,
            **({} if control_runner is None else {"runner": control_runner}),
            chat_environment={} if chat_environment is None else chat_environment,
        )
        request_text = request_payload.decode("utf-8", errors="strict")
        projection = control_state.chat_turn({
            "schemaVersion": 1,
            "requestId": f"chatreq-{hashlib.sha256(request_payload).hexdigest()[:32]}",
            "conversationHandle": None, "message": request_text,
        })
        final, _payload = assistant_runtime._private_json(
            proxy_receipt_path, "final Assistant model proxy receipt", MAX_PROXY_CONFIG_BYTES,
        )
        envelope = assistant_runtime.collect_private_turn(
            control_state=control_state, request_payload=request_payload, request_sha256=request_sha256,
            model_proxy_initial_receipt=initial, model_proxy_final_receipt=final,
            action_journal_roots=action_journal_roots,
        )
        selected = next(turn for turn in envelope["conversation"]["turns"] if turn["turnId"] == envelope["turnId"])
        assistant_evidence.validate_model_proxy_pair(initial, final, turn=selected, expected_model=expected_model)
        if projection.get("state") != "ready":
            raise evaluation.OutcomeError("Assistant portal projection did not settle ready")
        return envelope
    except UnicodeError as exc:
        raise evaluation.OutcomeError("Assistant system request is not strict UTF-8") from exc
    finally:
        _stop(process)
        deadline = time.monotonic() + PROXY_STOP_SECONDS
        while time.monotonic() < deadline and not _listener_absent(config["listenPort"]):
            time.sleep(0.05)
        if not _listener_absent(config["listenPort"]):
            raise evaluation.OutcomeError("Assistant model proxy listener survived teardown")
