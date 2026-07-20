"""ODS Model Switchboard support package.

PR 1 scope: the versioned model-state record only. The host agent is the
single writer; everything else reads. Stdlib-only by contract so the
standalone host agent can import it from the installed tree.
"""

from . import adapters, reconciler  # noqa: F401
from .state import (  # noqa: F401
    HISTORY_LIMIT,
    SCHEMA_VERSION,
    StateError,
    atomic_write_state,
    initial_state,
    initialize_if_missing,
    migrate_env_identity,
    read_state,
    record_verified_route,
    validate_state,
)

__all__ = [
    "HISTORY_LIMIT",
    "SCHEMA_VERSION",
    "StateError",
    "atomic_write_state",
    "initial_state",
    "initialize_if_missing",
    "migrate_env_identity",
    "read_state",
    "record_verified_route",
    "validate_state",
]
