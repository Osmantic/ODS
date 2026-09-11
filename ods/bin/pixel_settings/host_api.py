"""Owner-facing persistence; runtime activation is a separate lifecycle."""
import platform

from pixel_provider.store import StoreError
from pixel_provider.store_factory import existing_directory, prepare_directory, provider_directory
from .contract import SettingsError, validate_preferences
from .store import MAX_REVISION, create_settings_store, default_document, normalize_document
from .public import normalize_change, normalize_outcome, normalize_runtime, unavailable


def _response(document):
    return {"configuration": normalize_document(document),
            "runtime": {"status": "not-inspected", "reason": "runtime-status-separate"}}


def get_settings(data_dir):
    directory = provider_directory(data_dir)
    with existing_directory(directory) as exists:
        document = create_settings_store(directory).load() if exists else default_document()
    return _response(document)


def save_settings(data_dir, body):
    if (type(body) is not dict or set(body) != {"expectedRevision", "changes"}
            or type(body["expectedRevision"]) is not int
            or not 0 <= body["expectedRevision"] < MAX_REVISION):
        raise StoreError("invalid-request")
    try:
        changes = validate_preferences(body["changes"])
    except SettingsError:
        raise StoreError("invalid-request") from None
    directory = provider_directory(data_dir)
    prepare_directory(directory)
    document = create_settings_store(directory).save_changes(changes, expected_revision=body["expectedRevision"])
    return _response(document)


def runtime_status(data_dir, *, request=None):
    if platform.system() != "Linux":
        return unavailable("macos-launchd-adapter-missing" if platform.system() == "Darwin" else "native-windows-adapter-missing")
    if request is None:
        from pixel_access_client import request_access
        request = request_access
    try:
        status, value = request("settings-status", settings_data_dir=data_dir)
        if status != 200:
            # The controller reason is a fixed nonsecret code, never diagnostics.
            return normalize_runtime(unavailable(value.get("error", "settings-controller-unavailable")))
        return normalize_runtime(dict(value, schemaVersion=1, reason=None))
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable("settings-controller-unavailable")


def runtime_change(data_dir, body, *, request=None):
    try: body = normalize_change(body)
    except SettingsError: raise StoreError("invalid-request") from None
    if platform.system() != "Linux": raise StoreError("settings-platform-unavailable")
    if request is None:
        from pixel_access_client import request_access
        request = request_access
    status, value = request("settings-change", body, settings_data_dir=data_dir)
    if status != 200:
        # Validate the controller code without forwarding arbitrary upstream text.
        result = normalize_runtime(unavailable(value.get("error", "settings-transition-unavailable")))
        raise StoreError(result["reason"])
    result = normalize_outcome(value)
    if result["outcome"] == "applied" and result["appliedRevision"] != body["settingsRevision"]:
        raise SettingsError("settings-runtime-revision-mismatch")
    return result
