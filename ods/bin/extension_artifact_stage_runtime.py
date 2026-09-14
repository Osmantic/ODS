"""Compose dormant host-owned dependencies for exact artifact staging.

The installer owns directory creation and custody.  This module only validates
the fixed stage root and joins the reviewed verifier, immutable store, stage
adapter, and started-receipt observer.  Constructing the bundle registers no
dispatcher or observer and grants no lifecycle effect authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from extension_artifact_stage_adapter import ArtifactStageAdapter
from extension_artifact_stage_recovery import ArtifactStageRecoveryObserver
from extension_artifact_stage_store import (
    ArtifactStageStore,
    validate_artifact_stage_root,
)
from extension_artifact_verifier import HostArtifactRoots


@dataclass(frozen=True)
class ArtifactStageRuntime:
    """Unregistered exact dependencies for one fixed host stage root."""

    root: Path
    store: ArtifactStageStore
    dispatcher: ArtifactStageAdapter
    started_observer: ArtifactStageRecoveryObserver


def build_artifact_stage_runtime(
    *,
    data_dir: str | os.PathLike[str],
    builtin_root: str | os.PathLike[str],
    library_root: str | os.PathLike[str],
    user_root: str | os.PathLike[str],
) -> ArtifactStageRuntime:
    """Validate fixed custody and compose dependencies without registering them."""

    stage_root = validate_artifact_stage_root(
        Path(data_dir) / "assistant-first" / "artifact-stage"
    )
    store = ArtifactStageStore(stage_root)
    roots = HostArtifactRoots(
        builtin=builtin_root,
        library=library_root,
        user=user_root,
    )
    dispatcher = ArtifactStageAdapter(roots, store)
    observer = ArtifactStageRecoveryObserver(store)
    return ArtifactStageRuntime(
        root=stage_root,
        store=store,
        dispatcher=dispatcher,
        started_observer=observer,
    )


__all__ = [
    "ArtifactStageRuntime",
    "build_artifact_stage_runtime",
]
