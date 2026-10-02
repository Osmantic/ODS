import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

if os.name != "posix":
    raise unittest.SkipTest("Linux transaction process required")
source = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "transaction", source / "installers/windows/lib/remote-provider-transaction.py"
)
tx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tx)


class Transactions(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(
            tempfile.mkdtemp(dir=os.environ.get("ODS_TRANSACTION_TEST_ROOT"))
        )
        self.root = self.scratch / "install"
        self.source = self.scratch / "source"
        (self.root / "data/remote-provider/secrets").mkdir(parents=True)
        (self.root / "data/.extensions-lock").touch()
        self.secret = self.root / "data/remote-provider/secrets/private"
        self.secret.write_bytes(b"KEEP-PRIVATE")
        (self.source / "scripts").mkdir(parents=True)
        shutil.copyfile(
            source / "scripts/remote-provider-compose-selection.py",
            self.source / "scripts/remote-provider-compose-selection.py",
        )
        for service in tx.SERVICES:
            for base in (self.root, self.source):
                (base / "extensions/services" / service).mkdir(parents=True)
            self.marker(service, False, self.source).write_bytes(
                ("canonical-" + service).encode()
            )
        for name in ("docker-compose.base.yml", "docker-compose.override.yml"):
            (self.root / name).write_bytes(b"services: {}\n")

    def tearDown(self):
        self.assertEqual(self.secret.read_bytes(), b"KEEP-PRIVATE")
        shutil.rmtree(self.scratch)

    def marker(self, service, active, base=None):
        return (
            (base or self.root)
            / "extensions/services"
            / service
            / ("compose.yaml" if active else "compose.yaml.disabled")
        )

    def route(self, transport):
        p = self.root / "data/remote-provider/routing-state.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "ods.remote-routing-state.v1",
                    "enabled": True,
                    "provider": {"transport": transport},
                }
            )
        )
        return p.read_bytes()

    def sync(self):
        return tx.transact(self.root, self.source, {"operation": "sync"})

    def test_fresh(self):
        self.assertEqual(set(self.sync()["selection"].values()), {"disabled"})
        for service in tx.SERVICES:
            self.assertFalse(self.marker(service, True).exists())
            self.assertEqual(
                self.marker(service, False).read_bytes(),
                self.marker(service, False, self.source).read_bytes(),
            )

    def test_direct_and_ssh(self):
        for transport in ("direct", "ssh"):
            with self.subTest(transport=transport):
                for service in tx.SERVICES:
                    for active in (True, False):
                        self.marker(service, active).unlink(missing_ok=True)
                route = self.route(transport)
                self.sync()
                self.assertTrue(self.marker(tx.SERVICES[0], True).exists())
                self.assertTrue(
                    self.marker(tx.SERVICES[1], transport == "ssh").exists()
                )
                self.assertEqual(
                    (
                        self.root / "data/remote-provider/routing-state.json"
                    ).read_bytes(),
                    route,
                )

    def test_explicit_disabled_required_refused(self):
        self.route("direct")
        self.marker(tx.SERVICES[0], False).write_bytes(b"owner-disabled")
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(
            self.marker(tx.SERVICES[0], False).read_bytes(), b"owner-disabled"
        )

    def test_all_sources_validated_before_write(self):
        self.marker(tx.SERVICES[0], True).write_bytes(b"old")
        self.marker(tx.SERVICES[1], False, self.source).write_bytes(b"")
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.marker(tx.SERVICES[0], True).read_bytes(), b"old")

    def test_interruption_and_retry(self):
        self.route("ssh")
        original = tx.publish
        count = 0

        def interrupt(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("interrupted")
            return original(*args)

        with mock.patch.object(tx, "publish", side_effect=interrupt):
            with self.assertRaises(OSError):
                self.sync()
        self.sync()
        for service in tx.SERVICES:
            self.assertEqual(
                self.marker(service, True).read_bytes(),
                self.marker(service, False, self.source).read_bytes(),
            )
            self.assertFalse(self.marker(service, False).exists())

    def test_flags_resnapshot_and_override_precedence(self):
        self.sync()
        service = tx.SERVICES[0]
        self.marker(service, False).rename(self.marker(service, True))
        flags = ["-f", "docker-compose.base.yml", "-f", "docker-compose.override.yml"]
        result = tx.transact(
            self.root, self.source, {"operation": "flags", "flags": flags}
        )
        expected = [
            "-f",
            "docker-compose.base.yml",
            "-f",
            f"extensions/services/{service}/compose.yaml",
            "-f",
            "docker-compose.override.yml",
        ]
        self.assertEqual(result["flags"], expected)
        self.assertEqual((self.root / ".compose-flags").read_text(), " ".join(expected))
        self.marker(service, True).rename(self.marker(service, False))
        self.assertEqual(
            tx.transact(
                self.root, self.source, {"operation": "flags", "flags": expected}
            )["flags"],
            flags,
        )

    def test_flags_accept_native_windows_env_file_before_compose(self):
        self.sync()
        (self.root / ".env").write_text("ODS_TEST=1\n")
        (self.root / "docker-compose.nvidia.yml").write_text("services: {}\n")
        litellm = self.root / "extensions/services/litellm/compose.yaml"
        litellm.parent.mkdir(parents=True)
        litellm.write_text("services: {}\n")
        flags = [
            "--env-file", ".env",
            "-f", "docker-compose.base.yml",
            "-f", "docker-compose.nvidia.yml",
            "-f", "extensions/services/litellm/compose.yaml",
        ]
        result = tx.transact(
            self.root, self.source, {"operation": "flags", "flags": flags}
        )
        self.assertEqual(result["flags"], flags)
        self.assertEqual((self.root / ".compose-flags").read_text(), " ".join(flags))

    def test_flags_reject_invalid_env_file_pair_without_publication(self):
        self.sync()
        cache = self.root / ".compose-flags"
        cache.write_bytes(b"old-cache")
        base = ["-f", "docker-compose.base.yml"]
        for flags in (
            ["--env-file", "../outside", *base],
            ["--env-file", "other.env", *base],
            ["--env-file", ".env", "--env-file", ".env", *base],
            [*base, "--env-file", ".env"],
            ["--env-file", ".env", "-f", "docker-compose.nvidia.yml"],
        ):
            with self.subTest(flags=flags):
                with self.assertRaises(ValueError):
                    tx.transact(
                        self.root, self.source,
                        {"operation": "flags", "flags": flags},
                    )
                self.assertEqual(cache.read_bytes(), b"old-cache")

    def test_bad_flags_leave_cache(self):
        self.sync()
        (self.root / ".compose-flags").write_bytes(b"old-cache")
        for path in ("../outside", "C:\\outside", "/outside", "bad path.yml"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    tx.transact(
                        self.root,
                        self.source,
                        {"operation": "flags", "flags": ["-f", path]},
                    )
                self.assertEqual(
                    (self.root / ".compose-flags").read_bytes(), b"old-cache"
                )
        with self.assertRaises(ValueError):
            tx.transact(
                self.root,
                self.source,
                {
                    "operation": "flags",
                    "flags": [
                        "-f",
                        "extensions/services/remote-provider-egress/compose.yaml",
                    ],
                },
            )
        self.assertEqual((self.root / ".compose-flags").read_bytes(), b"old-cache")

    def test_lock_hardlink_rejected(self):
        (self.root / "data/.extensions-lock").unlink()
        os.link(self.secret, self.root / "data/.extensions-lock")
        with self.assertRaises(ValueError):
            self.sync()

    def test_target_hardlink_rejected(self):
        os.link(self.secret, self.marker(tx.SERVICES[0], False))
        with self.assertRaises(ValueError):
            self.sync()

    def test_source_symlink_rejected(self):
        target = self.marker(tx.SERVICES[1], False, self.source)
        target.unlink()
        target.symlink_to(self.secret)
        with self.assertRaises((OSError, ValueError)):
            self.sync()
        self.assertFalse(self.marker(tx.SERVICES[0], False).exists())

    def test_dual_markers_rejected(self):
        self.marker(tx.SERVICES[0], True).write_bytes(b"active")
        self.marker(tx.SERVICES[0], False).write_bytes(b"disabled")
        with self.assertRaises(ValueError):
            self.sync()

    def test_helper_does_not_mutate_secrets(self):
        self.route("ssh")
        self.sync()
        tx.transact(
            self.root,
            self.source,
            {"operation": "flags", "flags": ["-f", "docker-compose.base.yml"]},
        )
        self.assertEqual(self.secret.read_bytes(), b"KEEP-PRIVATE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
