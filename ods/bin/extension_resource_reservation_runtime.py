"""Compose dormant host-owned dependencies for exact reservation runtime and operations.

The installer owns directory creation and custody.  This module only validates
the fixed reservation root and joins the reviewed store and reservation
adapter.  Constructing the bundle registers no dispatcher or observer and
grants no lifecycle effect authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from extension_resource_reservation_adapter import (
    ResourceReservationAdapter,
)
from extension_resource_reservation_release import (
    ResourceReleaseAdapter,
)
from extension_resource_reservation_store import (
    ResourceReservationStore,
)


@dataclass(frozen=True)
class ResourceReservationRuntime:
    """Unregistered exact dependencies for one fixed host reservation root."""

    root: Path
    store: ResourceReservationStore
    reserve_dispatcher: ResourceReservationAdapter
    release_dispatcher: ResourceReleaseAdapter


def build_resource_reservation_runtime(
    *,
    data_dir: str | os.PathLike[str],
) -> ResourceReservationRuntime:
    """Validate fixed custody and compose dependencies without registering them."""

    reservation_root = Path(data_dir) / "assistant-first" / "resource-reservations"
    store = ResourceReservationStore(reservation_root)
    reserve_dispatcher = ResourceReservationAdapter(store)
    release_dispatcher = ResourceReleaseAdapter(store)
    return ResourceReservationRuntime(
        root=reservation_root,
        store=store,
        reserve_dispatcher=reserve_dispatcher,
        release_dispatcher=release_dispatcher,
    )


__all__ = [
    "ResourceReservationRuntime",
    "build_resource_reservation_runtime",
]
