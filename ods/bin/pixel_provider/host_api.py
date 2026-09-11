"""Host-owned provider Settings. Saving is not runtime activation."""

import os
import platform

from .config import default_config, public_config
from .public import (
    from_controller,
    normalize_change,
    normalize_outcome,
    safe_reason,
    unavailable,
)
from .store import StoreError
from .store_factory import (
    credential_store,
    existing_directory,
    prepare_directory,
    provider_directory,
    provider_store,
)
from .vault import validate_edit


def _directory(data_dir):
    """Compatibility boundary for existing, still-POSIX scope storage callers.

    Settings uses provider_directory directly. Keeping this guard prevents the
    separate ScopeStore from silently claiming native Windows qualification.
    """
    if os.name != "posix":
        raise StoreError("unsupported-platform")
    return provider_directory(data_dir)


def get_configuration(data_dir):
    directory = provider_directory(data_dir)
    with existing_directory(directory) as exists:
        if not exists:
            # A pristine install has no feature state and requires no migration.
            return public_config(default_config())
        return public_config(provider_store(directory).load())


def save_configuration(data_dir, body):
    body = validate_edit(body)
    directory = provider_directory(data_dir)
    # No recursive mkdir/chmod or silent repair of existing state. The install's
    # data directory must already exist. Store checks custody before any write.
    prepare_directory(directory)
    return credential_store(directory).save_public(body)


def runtime_status(data_dir, *, request=None):
    if platform.system() != "Linux":
        return unavailable("macos-launchd-adapter-missing" if platform.system() == "Darwin" else "native-windows-adapter-missing")
    if request is None:
        from pixel_access_client import request_access
        request = request_access
    try:
        status, value = request("provider-status", settings_data_dir=data_dir)
        if status != 200:
            return unavailable(safe_reason(value.get("error") if isinstance(value, dict) else None))
        return from_controller(value)
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable("provider-controller-unavailable")


def runtime_change(data_dir, body, *, request=None):
    try:
        body = normalize_change(body)
    except ValueError:
        raise StoreError("invalid-request") from None
    if platform.system() != "Linux":
        raise StoreError("provider-transition-unavailable")
    if request is None:
        from pixel_access_client import request_access
        request = request_access
    try:
        status, value = request("provider-change", body, settings_data_dir=data_dir)
    except (OSError, ValueError, TypeError, KeyError):
        raise StoreError("provider-transition-uncertain") from None
    if status != 200:
        reason = value.get("error") if isinstance(value, dict) else None
        raise StoreError(safe_reason(reason, "provider-transition-unavailable"))
    try:
        return normalize_outcome(value, body)
    except (ValueError, TypeError, KeyError):
        # A bad/lost response is not evidence that the root operation failed.
        raise StoreError("provider-transition-uncertain") from None
