"""Full source-updater fixtures: real temp files, no real Git/Docker/HTTP/agent.

Snapshot creation/restoration, Compose argument parsing and version publication
run unchanged. Only external service boundaries and migration input are stubbed.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


UPDATER = Path(__file__).resolve().parents[1] / "ods-update.sh"

# Unknown operations fail closed, never delegate to host service executables.
DRIVER = r'''
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["ODS_UPDATE_FIXTURE"])
install = root / "install with spaces"
settings = json.loads((root / "settings.json").read_text())
state_path = root / "state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
if tool == "driver.py":
    tool, args = args[0], args[1:]
version = json.loads((install / ".version").read_text())["version"]

def emit(kind, **extra):
    with (root / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(kind=kind, tool=tool, argv=args,
                                     cwd=os.getcwd(), version=version, **extra)) + "\n")

def fixture_error(exc_type, value, traceback):
    # Some production probes suppress stderr. Such suppression must never let
    # a broken stub masquerade as the intentional failure a test requested.
    emit("fixture-error", error=str(value))
    sys.__excepthook__(exc_type, value, traceback)

sys.excepthook = fixture_error

def count(key):
    state[key] = state.get(key, 0) + 1
    state_path.write_text(json.dumps(state))
    return state[key]

def stop(code=0):
    sys.exit(code)

if tool == "git":
    rest = args[:]
    if rest[:1] == ["-C"]:
        assert Path(rest[1]) == install, rest
        rest = rest[2:]
    if rest[:1] == ["rev-parse"]:
        emit("git-identity")
        outputs = {("--is-inside-work-tree",): "true", ("--show-toplevel",): str(install),
                   ("--show-prefix",): "", ("--short", "HEAD"): "abcdef01"}
        assert tuple(rest[1:]) in outputs, rest
        print(outputs[tuple(rest[1:])])
        stop()
    if rest == ["ls-files", "--error-unmatch", "ods-update.sh"]:
        emit("git-tracked-file")
        print("ods-update.sh")
        stop()
    if rest == ["branch", "--show-current"]:
        emit("git-branch")
        print("" if settings.get("detached") else "main")
        stop()
    if rest == ["fetch", "origin"]:
        emit("fetch")
        stop(1 if settings.get("fetch_fail") else 0)
    if rest == ["pull", "--ff-only", "origin", "main"]:
        emit("pull")
        stop(1 if settings.get("pull_fail") else 0)
    if rest == ["describe", "--tags"]:
        emit("describe")
        print("v2.0.0")
        stop()
elif tool in ("docker", "docker-compose"):
    if tool == "docker" and args == ["info"]:
        emit("docker-info")
        stop()
    if tool == "docker":
        assert args[:1] == ["compose"], args
        rest = args[1:]
    else:
        rest = args[:]
    if rest == ["version"]:
        emit("compose-version")
        stop(1 if settings.get("v2_absent") and tool == "docker" else 0)
    flags = []
    while rest[:1] == ["-f"]:
        flags.append(rest[1])
        rest = rest[2:]
    assert flags == settings["compose_files"], (flags, settings["compose_files"])
    assert all((install / name).is_file() for name in flags)
    command = rest[0] if rest else ""
    emit(command, compose_files=flags)
    if command == "build":
        assert rest == ["build"], rest
        stop(1 if settings.get("build_fail") else 0)
    if command == "down":
        assert rest == ["down", "--remove-orphans"], rest
        n = count("down")
        stop(1 if (settings.get("down_fail_once") and n == 1)
             or (settings.get("rollback_down_fail") and n >= 2) else 0)
    if command == "up":
        assert rest == ["up", "-d", "--no-build"], rest
        n = count("up")
        stop(1 if (settings.get("up_fail_once") and n == 1)
             or (settings.get("rollback_up_fail") and n >= 2) else 0)
    if command == "config":
        if rest == ["config", "--services"]:
            print("\n".join(settings.get("services", ["dashboard-api", "llama-server"])))
            stop()
        if rest == ["config", "--format", "json"]:
            assert settings.get("container_state") == "exited", settings
            assert settings.get("container_exit_code") == 0, settings
            emit("config-json")
            print(json.dumps({"services": settings.get("service_config", {})}))
            stop()
        assert False, rest
    if command == "ps":
        assert rest[1:4] == ["--all", "--format", "json"], rest
        services = rest[4:] or settings.get("services", ["dashboard-api", "llama-server"])
        containers = [dict(Service=s, State=settings.get("container_state", "running"),
                           Health=settings.get("container_health", "healthy"),
                           ExitCode=settings.get("container_exit_code", 0)) for s in services]
        if settings.get("container_missing"):
            containers = []
        if settings.get("ps_json_lines"):
            print("\n".join(json.dumps(container) for container in containers))
        else:
            print(json.dumps(containers))
        stop()
elif tool == "curl":
    url = args[-1]
    assert url in settings.get("allowed_urls", [
        "http://127.0.0.1:31992/health", "http://127.0.0.1:31992/api/status",
        "http://127.0.0.1:31993/v1/models",
    ]), url
    emit("http", url=url)
    stop(1 if settings.get("http_fail") else 0)
elif tool == "cli":
    assert os.environ.get("INSTALL_DIR") == str(install)
    if args == ["agent", "restart"]:
        emit("agent-restart")
        stop(1 if settings.get("agent_restart_fail") else 0)
    if args == ["agent", "status"]:
        emit("agent-status")
        stop(1 if settings.get("agent_unhealthy") else 0)
elif tool == "migration":
    emit("migration")
    with (install / ".env").open("a") as stream:
        stream.write("MIGRATION=changed\n")
    config = install / "config/litellm/settings.yaml"
    config.write_text("migration: changed\n")
    (config.parent / "new-migration-file").write_text("new\n")
    if settings.get("corrupt_snapshot"):
        snapshots = list((install / "data/backups").glob("pre-update-*/snapshot.json"))
        assert len(snapshots) == 1, snapshots
        snapshots[0].write_text("not JSON")
    stop(1 if settings.get("migration_fail") else 0)
elif tool == "cp":
    destination = Path(args[-1]).resolve()
    assert root in destination.parents, destination
    if (settings.get("snapshot_copy_fail") and str(install / ".env") in args
            and install / "data/backups" in destination.parents):
        emit("snapshot-copy-failed")
        stop(1)
    os.execv(settings["real_cp"], [settings["real_cp"]] + args)
emit("unexpected")
print("Unexpected fixture command: " + tool + " " + repr(args), file=sys.stderr)
stop(91)
'''


@unittest.skipUnless(os.name == "posix", "The source updater is a POSIX Bash program")
class SourceUpdateActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        cls.jq = shutil.which("jq")
        if not cls.bash or not cls.jq:
            raise unittest.SkipTest("Integration fixtures require Bash and jq")
        major = subprocess.check_output(
            [cls.bash, "-c", 'printf "%s" "${BASH_VERSINFO[0]}"'], text=True
        )
        if int(major) < 4:
            raise unittest.SkipTest("Source activation requires Bash 4+")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-source-update-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "install with spaces"
        self.install.mkdir()
        self.bin = self.root / "stub-bin"
        self.bin.mkdir()
        (self.root / "home").mkdir()
        (self.root / "tmp").mkdir()
        self.write(self.install / "ods-update.sh", UPDATER.read_text(encoding="utf-8"), 0o700)
        self.write(self.install / ".version", '{"version":"v1.0.0","sentinel":"keep"}\n', 0o600)
        self.write(self.install / ".env", "ODS_VERSION=v1.0.0\nDASHBOARD_API_PORT=31992\nOLLAMA_PORT=31993\n", 0o600)
        self.write(self.install / "config/litellm/settings.yaml", "original: configuration\n", 0o640)
        self.write(self.install / "bin/ods-host-agent.py", "# fixture; never executed\n")
        self.write(self.bin / "driver.py", "#!" + sys.executable + "\n" + DRIVER, 0o700)
        for command in ("git", "docker", "docker-compose", "curl", "cp"):
            (self.bin / command).symlink_to("driver.py")
        # jq remains real even when it was installed outside the system PATH.
        (self.bin / "jq").symlink_to(self.jq)
        self.write(self.install / "ods-cli", '#!/usr/bin/env bash\nexec "$ODS_FIXTURE_PYTHON" "$ODS_UPDATE_FIXTURE/stub-bin/driver.py" cli "$@"\n', 0o700)
        self.write(self.install / "migrations/migrate-v2.sh", '#!/usr/bin/env bash\nexec "$ODS_FIXTURE_PYTHON" "$ODS_UPDATE_FIXTURE/stub-bin/driver.py" migration\n', 0o700)
        self.settings = {"compose_files": ["docker-compose.yml"], "real_cp": shutil.which("cp")}
        self.write(self.install / "docker-compose.yml", "services: {}\n")
        self.write(self.install / ".compose-flags", "-f docker-compose.yml\n")
        self.original_version = (self.install / ".version").read_bytes()
        self.original_env = (self.install / ".env").read_bytes()
        self.original_config = (self.install / "config/litellm/settings.yaml").read_bytes()
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "HOME": str(self.root / "home"), "TMPDIR": str(self.root / "tmp"),
            "LC_ALL": "C", "HEALTH_TIMEOUT": "1",
            "ODS_UPDATE_FIXTURE": str(self.root), "ODS_FIXTURE_PYTHON": sys.executable,
        }

    @staticmethod
    def write(path, text, mode=0o600):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        path.chmod(mode)

    def run_update(self, **settings):
        self.settings.update(settings)
        self.write(self.root / "settings.json", json.dumps(self.settings))
        result = subprocess.run(
            [self.bash, str(self.install / "ods-update.sh"), "update"],
            cwd=self.root, env=self.env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=20,
        )
        self.output = result.stdout
        log = self.root / "events.jsonl"
        self.events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        self.assertNotIn("unexpected", self.kinds(), self.output)
        self.assertNotIn("fixture-error", self.kinds(), self.events)
        self.assertNotIn("Traceback", self.output, self.output)
        return result

    def use_bind_address(self, address, default_ports=False):
        content = "ODS_VERSION=v1.0.0\nBIND_ADDRESS=" + address + "\n"
        if not default_ports:
            content += "DASHBOARD_API_PORT=31992\nOLLAMA_PORT=31993\n"
        self.write(self.install / ".env", content, 0o600)
        self.original_env = (self.install / ".env").read_bytes()

    def assert_probed_urls(self, result, expected):
        self.assertEqual(result.returncode, 0, self.output)
        actual = [event["url"] for event in self.events if event["kind"] == "http"]
        self.assertEqual(actual, expected)

    def kinds(self):
        return [event["kind"] for event in self.events]

    def assert_not_published(self, result):
        self.assertNotEqual(result.returncode, 0, self.output)
        self.assertEqual((self.install / ".version").read_bytes(), self.original_version)
        self.assertNotIn("Update complete!", self.output)
        self.assertNotIn("describe", self.kinds())

    def assert_restored(self):
        self.assertEqual((self.install / ".env").read_bytes(), self.original_env)
        self.assertEqual((self.install / ".env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.install / "config/litellm/settings.yaml").read_bytes(), self.original_config)
        self.assertEqual((self.install / "config/litellm/settings.yaml").stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.install / "config/litellm/new-migration-file").exists())
        self.assertFalse(list(self.install.rglob(".ods-restore.*")))

    def assert_no_v1_execution(self):
        self.assertFalse(any(event["tool"] == "docker-compose" for event in self.events), self.events)

    def test_success_builds_migrates_restarts_agent_checks_health_then_records_version(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, self.output)
        required = ["fetch", "pull", "build", "migration", "down", "up",
                    "agent-restart", "agent-status", "config", "ps", "http", "describe"]
        positions = [self.kinds().index(kind) for kind in required]
        self.assertEqual(positions, sorted(positions), self.events)
        self.assertTrue(all(event["version"] == "v1.0.0" for event in self.events))
        version = json.loads((self.install / ".version").read_text())
        self.assertEqual(version["version"], "v2.0.0")
        self.assertEqual(version["sentinel"], "keep")
        self.assertTrue(version["last_update"])
        snapshot = Path(version["last_rollback_point"])
        self.assertEqual(snapshot.parent, self.install / "data/backups")
        self.assertEqual((snapshot / ".version").read_bytes(), self.original_version)
        self.assertEqual((snapshot / ".env").read_bytes(), self.original_env)
        self.assertEqual((snapshot / "config-litellm/settings.yaml").read_bytes(), self.original_config)
        self.assert_no_v1_execution()

    def test_build_failure_never_migrates_stops_services_restarts_agent_or_records_version(self):
        result = self.run_update(build_fail=True)
        self.assert_not_published(result)
        self.assertIn("build", self.kinds())
        for kind in ("migration", "down", "up", "agent-restart", "http"):
            self.assertNotIn(kind, self.kinds())
        self.assertEqual((self.install / ".env").read_bytes(), self.original_env)
        self.assert_no_v1_execution()

    def test_v1_only_installation_is_rejected_before_snapshot_or_service_changes(self):
        result = self.run_update(v2_absent=True)
        self.assert_not_published(result)
        self.assertFalse((self.install / "data/backups").exists())
        for kind in ("fetch", "pull", "build", "migration", "down", "up", "agent-restart"):
            self.assertNotIn(kind, self.kinds())
        self.assert_no_v1_execution()

    def test_snapshot_copy_failure_aborts_before_git_or_service_mutation(self):
        result = self.run_update(snapshot_copy_fail=True)
        self.assert_not_published(result)
        self.assertIn("snapshot-copy-failed", self.kinds())
        self.assertEqual((self.install / ".env").read_bytes(), self.original_env)
        self.assertEqual((self.install / "config/litellm/settings.yaml").read_bytes(), self.original_config)
        for kind in ("fetch", "pull", "build", "migration", "down", "up", "agent-restart"):
            self.assertNotIn(kind, self.kinds())

    def test_quoted_compose_paths_survive_activation_and_restore_without_evaluation(self):
        files = ["stack files/base.yml", "stack files/owner's $(touch INJECTED).yml"]
        for name in files:
            self.write(self.install / name, "services: {}\n")
        self.write(self.install / ".compose-flags", " ".join("-f " + shlex.quote(name) for name in files) + "\n")
        result = self.run_update(compose_files=files, up_fail_once=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertEqual(self.kinds().count("up"), 2)
        self.assertFalse(list(self.root.rglob("INJECTED")))
        self.assert_no_v1_execution()

    def test_detached_checkout_fails_before_snapshot_or_service_changes(self):
        result = self.run_update(detached=True)
        self.assert_not_published(result)
        self.assertFalse((self.install / "data/backups").exists())
        for kind in ("fetch", "pull", "build", "down", "up", "agent-restart"):
            self.assertNotIn(kind, self.kinds())

    def test_fetch_failure_does_not_pull_build_or_stop_services(self):
        result = self.run_update(fetch_fail=True)
        self.assert_not_published(result)
        for kind in ("pull", "build", "migration", "down", "up", "agent-restart"):
            self.assertNotIn(kind, self.kinds())

    def test_pull_failure_does_not_build_or_stop_services(self):
        result = self.run_update(pull_fail=True)
        self.assert_not_published(result)
        self.assertIn("fetch", self.kinds())
        self.assertIn("pull", self.kinds())
        for kind in ("build", "migration", "down", "up", "agent-restart"):
            self.assertNotIn(kind, self.kinds())

    def test_migration_failure_restores_real_snapshot_and_never_starts_updated_agent(self):
        self.write(self.install / "migrations/migrate-v3.sh", '#!/usr/bin/env bash\nprintf ran > "$ODS_UPDATE_FIXTURE/second-migration"\n', 0o700)
        result = self.run_update(migration_fail=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertEqual(self.kinds().count("down"), 1)
        self.assertEqual(self.kinds().count("up"), 1)
        self.assertNotIn("agent-restart", self.kinds())
        self.assertFalse((self.root / "second-migration").exists())

    def test_v2_down_error_restores_without_falling_back_to_v1(self):
        result = self.run_update(down_fail_once=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertEqual(self.kinds().count("down"), 2)
        self.assertEqual(self.kinds().count("up"), 1)
        self.assertNotIn("agent-restart", self.kinds())
        self.assert_no_v1_execution()

    def test_v2_up_error_restores_without_falling_back_to_v1(self):
        result = self.run_update(up_fail_once=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertEqual(self.kinds().count("up"), 2)
        self.assert_no_v1_execution()

    def test_failed_rollback_restart_reports_manual_recovery_and_never_completes(self):
        result = self.run_update(up_fail_once=True, rollback_up_fail=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("Manual recovery required", self.output)
        self.assert_no_v1_execution()

    def test_failed_rollback_down_does_not_attempt_rollback_up(self):
        result = self.run_update(down_fail_once=True, rollback_down_fail=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertEqual(self.kinds().count("down"), 2)
        self.assertNotIn("up", self.kinds())
        self.assertIn("Manual recovery required", self.output)
        self.assert_no_v1_execution()

    def test_corrupt_snapshot_fails_restore_without_claiming_rollback_succeeded(self):
        result = self.run_update(migration_fail=True, corrupt_snapshot=True)
        self.assert_not_published(result)
        self.assertIn("Snapshot restore failed", self.output)
        self.assertNotEqual((self.install / ".env").read_bytes(), self.original_env)
        self.assertNotIn("down", self.kinds())
        self.assertNotIn("up", self.kinds())

    def test_agent_restart_failure_restores_configuration_and_does_not_publish(self):
        result = self.run_update(agent_restart_fail=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("agent-restart", self.kinds())
        self.assertNotIn("agent-status", self.kinds())
        self.assertNotIn("http", self.kinds())

    def test_agent_that_stays_unhealthy_cannot_publish(self):
        result = self.run_update(agent_unhealthy=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("agent-status", self.kinds())
        self.assertNotIn("http", self.kinds())

    def test_running_container_with_unhealthy_healthcheck_cannot_publish(self):
        result = self.run_update(container_health="unhealthy")
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("ps", self.kinds())

    def test_configured_service_missing_from_ps_cannot_publish(self):
        result = self.run_update(container_missing=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("ps", self.kinds())

    def test_compose_v2_json_lines_health_output_is_supported(self):
        result = self.run_update(ps_json_lines=True)
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(json.loads((self.install / ".version").read_text())["version"], "v2.0.0")

    def test_core_dashboard_api_http_failure_cannot_publish(self):
        result = self.run_update(http_fail=True)
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("http", self.kinds())

    def test_absent_optional_services_do_not_trigger_unrelated_http_probes(self):
        result = self.run_update(services=["dashboard-api"])
        self.assertEqual(result.returncode, 0, self.output)
        urls = [event["url"] for event in self.events if event["kind"] == "http"]
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("http://127.0.0.1:31992/") for url in urls), urls)

    def test_specific_lan_bind_is_probed_instead_of_loopback(self):
        self.use_bind_address("192.0.2.23")
        urls = ["http://192.0.2.23:31992/health", "http://192.0.2.23:31993/v1/models"]
        self.assert_probed_urls(self.run_update(allowed_urls=urls), urls)

    def test_ipv6_bind_has_brackets_and_default_compose_ports(self):
        self.use_bind_address("2001:db8::23", default_ports=True)
        urls = ["http://[2001:db8::23]:3002/health", "http://[2001:db8::23]:11434/v1/models"]
        self.assert_probed_urls(self.run_update(allowed_urls=urls), urls)

    def test_ipv4_wildcard_is_probed_at_loopback(self):
        self.use_bind_address("0.0.0.0")
        urls = ["http://127.0.0.1:31992/health", "http://127.0.0.1:31993/v1/models"]
        self.assert_probed_urls(self.run_update(allowed_urls=urls), urls)

    def test_ipv6_wildcard_is_probed_at_bracketed_loopback(self):
        self.use_bind_address("[::]")
        urls = ["http://[::1]:31992/health", "http://[::1]:31993/v1/models"]
        self.assert_probed_urls(self.run_update(allowed_urls=urls), urls)

    def test_declared_oneshot_exit_zero_with_restart_no_can_complete(self):
        result = self.run_update(
            services=["fixture-task"], container_state="exited", container_exit_code=0,
            service_config={"fixture-task": {"labels": {"com.ods.lifecycle": "oneshot"}, "restart": "no"}},
        )
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn("config-json", self.kinds())
        self.assertNotIn("http", self.kinds())
        self.assertEqual(json.loads((self.install / ".version").read_text())["version"], "v2.0.0")

    def test_undeclared_exit_zero_service_cannot_complete(self):
        result = self.run_update(
            services=["fixture-task"], container_state="exited", container_exit_code=0,
            service_config={"fixture-task": {"restart": "no"}},
        )
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("config-json", self.kinds())

    def test_declared_oneshot_nonzero_exit_cannot_complete(self):
        result = self.run_update(
            services=["fixture-task"], container_state="exited", container_exit_code=7,
            service_config={"fixture-task": {"labels": {"com.ods.lifecycle": "oneshot"}, "restart": "no"}},
        )
        self.assert_not_published(result)
        self.assert_restored()
        self.assertNotIn("config-json", self.kinds())

    def test_oneshot_label_with_automatic_restart_cannot_complete(self):
        result = self.run_update(
            services=["fixture-task"], container_state="exited", container_exit_code=0,
            service_config={"fixture-task": {"labels": {"com.ods.lifecycle": "oneshot"}, "restart": "always"}},
        )
        self.assert_not_published(result)
        self.assert_restored()
        self.assertIn("config-json", self.kinds())


if __name__ == "__main__":
    unittest.main(verbosity=2)
