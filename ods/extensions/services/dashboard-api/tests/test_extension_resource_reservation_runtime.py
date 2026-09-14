"""Dormant reservation runtime composition tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_resource_reservation_adapter as reservation_adapter  # noqa: E402, RUF100
import extension_resource_reservation_release as reservation_release  # noqa: E402, RUF100
import extension_resource_reservation_runtime as reservation_runtime  # noqa: E402, RUF100
import extension_resource_reservation_store as reservations  # noqa: E402, RUF100

SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
)


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)


@unittest.skipUnless(SUPPORTED, "requires Linux dir_fd semantics")
class ReservationRuntimeTests(unittest.TestCase):
    def test_builds_exact_unregistered_dependencies_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            reservation_root = data / "assistant-first" / "resource-reservations"
            _private_directory(reservation_root)

            composed = reservation_runtime.build_resource_reservation_runtime(
                data_dir=data
            )

            self.assertIs(
                type(composed), reservation_runtime.ResourceReservationRuntime
            )
            self.assertEqual(composed.root, reservation_root)
            self.assertIs(type(composed.store), reservations.ResourceReservationStore)
            self.assertIs(
                type(composed.reserve_dispatcher),
                reservation_adapter.ResourceReservationAdapter,
            )
            self.assertIs(
                type(composed.release_dispatcher),
                reservation_release.ResourceReleaseAdapter,
            )
            self.assertEqual(list(reservation_root.iterdir()), [])

    def test_root_validation_fails_closed_without_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                reservation_runtime.build_resource_reservation_runtime(data_dir=data)
            self.assertEqual(ctx.exception.code, "root-missing")
            self.assertFalse((data / "assistant-first").exists())

    def test_symlinked_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            parent = data / "assistant-first"
            target = base / "target"
            _private_directory(parent)
            _private_directory(target)
            (parent / "resource-reservations").symlink_to(
                target, target_is_directory=True
            )

            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                reservation_runtime.build_resource_reservation_runtime(data_dir=data)
            self.assertIn(
                ctx.exception.code,
                {"root-invalid", "root-io-error"},
            )

    def test_wrong_mode_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            reservation_root = data / "assistant-first" / "resource-reservations"
            _private_directory(reservation_root)
            reservation_root.chmod(0o755)

            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                reservation_runtime.build_resource_reservation_runtime(data_dir=data)
            self.assertEqual(ctx.exception.code, "root-custody-violation")

    def test_build_no_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            reservation_root = data / "assistant-first" / "resource-reservations"
            _private_directory(reservation_root)

            before = {str(p) for p in reservation_root.rglob("*")}
            reservation_runtime.build_resource_reservation_runtime(data_dir=data)
            after = {str(p) for p in reservation_root.rglob("*")}

            self.assertEqual(before, after)

    def test_runtime_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            reservation_root = data / "assistant-first" / "resource-reservations"
            _private_directory(reservation_root)

            composed = reservation_runtime.build_resource_reservation_runtime(
                data_dir=data
            )
            with self.assertRaises(FrozenInstanceError):
                composed.root = Path("/tmp")  # type: ignore[misc]

    def test_public_names_are_exact(self) -> None:
        """Defect 4: verify exact public class/function names."""
        self.assertIs(
            reservation_runtime.ResourceReservationRuntime,
            reservation_runtime.ResourceReservationRuntime,
        )
        self.assertIs(
            reservation_runtime.build_resource_reservation_runtime,
            reservation_runtime.build_resource_reservation_runtime,
        )
        self.assertEqual(
            set(reservation_runtime.__all__),
            {"ResourceReservationRuntime", "build_resource_reservation_runtime"},
        )

    def test_production_unreachability(self) -> None:
        """Production code does not import the reservation runtime or adapter."""
        repo = Path(__file__).resolve().parents[5]

        agent_path = BIN_DIR / "ods-host-agent.py"
        agent_source = agent_path.read_text(encoding="utf-8")
        self.assertNotIn("extension_resource_reservation_runtime", agent_source)
        self.assertNotIn("extension_resource_reservation_adapter", agent_source)

        dashboard_api = repo / "ods" / "extensions" / "services" / "dashboard-api"
        for path in dashboard_api.rglob("*.py"):
            if "tests" in path.parts:
                continue
            if path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "extension_resource_reservation_runtime",
                source,
                f"unexpected production import in {path.relative_to(repo)}",
            )
            self.assertNotIn(
                "extension_resource_reservation_adapter",
                source,
                f"unexpected production import in {path.relative_to(repo)}",
            )


if __name__ == "__main__":
    unittest.main()
