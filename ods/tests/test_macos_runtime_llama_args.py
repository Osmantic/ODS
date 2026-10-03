"""Native macOS llama-server flags against real llama.cpp --help output.

ODS installs keep the llama-server they were installed with, so every flag a
macOS launcher passes must work on each runtime ODS has pinned: b8210 (older
installs) and b9014 (current pin). The fixtures are verbatim `llama-server
--help` captures:

  tests/fixtures/llama-server-help/b8210.txt  llama-b8210-bin-macos-arm64 on the Mac mini M4
  tests/fixtures/llama-server-help/b9014.txt  llama-b9014-bin-macos-arm64 on the Mac mini M4

b8210 rejects --spec-draft-n-max (its flag is --draft-max), and b9014 lists
--draft-max as removed. A launcher that passed --spec-draft-n-max stopped the
b8210 server from starting; test_static_launcher_flags_work_on_every_pinned_runtime
fails on that.

Run from ods/:  python3 -m unittest tests/test_macos_runtime_llama_args.py
"""
import ast
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/llama-server-help"
SOURCE = ROOT / "installers/macos/lib/native-checkpoint-args.py"
spec = importlib.util.spec_from_file_location("native_checkpoint_args", SOURCE)
qualifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualifier)

HELP = {path.stem: path.read_text(encoding="utf-8") for path in sorted(FIXTURES.glob("b*.txt"))}
EMPTY = ("", "", "", "", "")


def pinned_release():
    text = (ROOT / "installers/macos/lib/constants.sh").read_text(encoding="utf-8")
    return re.search(r'^LLAMA_CPP_RELEASE_TAG="(b\d+)"', text, re.M).group(1)


def run_with_help(help_text, *args, **kwargs):
    with patch.object(qualifier.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0, help_text, "")) as run:
        return qualifier.qualify("/runtime", *args, **kwargs), run


def array_flags(text, name):
    """--flags inside bash `name=(...)` and `name+=(...)` array literals."""
    flags = set()
    for match in re.finditer(re.escape(name) + r"\+?=\(", text):
        depth, index = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        body = text[match.end():index - 1]
        flags.update(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", body))
    return flags


def section(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def host_agent_flags():
    """Flags _launch_native_llama_server passes itself on macOS."""
    tree = ast.parse((ROOT / "bin/ods-host-agent.py").read_text(encoding="utf-8"))
    qualified = set()
    launch = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_MACOS_QUALIFIED_DRAFT_KEYS" for t in node.targets):
            qualified = {pair.elts[0].value for pair in node.value.elts}
        if isinstance(node, ast.FunctionDef) and node.name == "_launch_native_llama_server":
            launch = node
    assert launch is not None and qualified, "host agent launch function or draft keys not found"
    flags, mapped = set(), set()
    for node in ast.walk(launch):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "optional_args":
            for key, value in zip(node.value.keys, node.value.values):
                mapped.add(id(value))
                if key.value not in qualified:  # popped on macOS
                    flags.add(value.value)
    for node in ast.walk(launch):
        if (isinstance(node, ast.Constant) and id(node) not in mapped and isinstance(node.value, str)
                and re.fullmatch(r"--[a-z][a-z0-9-]*", node.value)):
            flags.add(node.value)
    return flags, qualified


class HelpFixtureTests(unittest.TestCase):
    def test_fixtures_cover_both_shipped_runtimes(self):
        self.assertLessEqual({"b8210", "b9014"}, set(HELP))

    def test_b8210_rejects_the_renamed_draft_flag(self):
        self.assertFalse(qualifier.supported(HELP["b8210"], "--spec-draft-n-max"))
        self.assertTrue(qualifier.supported(HELP["b8210"], "--draft-max"))
        self.assertFalse(qualifier.supported(HELP["b8210"], "--spec-draft-type-k"))
        self.assertTrue(qualifier.supported(HELP["b8210"], "--cache-type-k-draft"))

    def test_b9014_lists_the_old_draft_flag_as_removed(self):
        self.assertTrue(qualifier.supported(HELP["b9014"], "--spec-draft-n-max"))
        self.assertFalse(qualifier.supported(HELP["b9014"], "--draft-max"))
        self.assertTrue(qualifier.supported(HELP["b9014"], "--spec-draft-type-k"))

    def test_only_b9014_has_the_benchmarked_ngram_mod(self):
        self.assertFalse(qualifier.supported(HELP["b8210"], qualifier.NGRAM_MOD_CAPABILITY))
        self.assertTrue(qualifier.supported(HELP["b9014"], qualifier.NGRAM_MOD_CAPABILITY))

    def test_only_b9014_has_the_reasoning_switch(self):
        # b8210's --reasoning-format / --reasoning-budget must not count.
        self.assertFalse(qualifier.supported(HELP["b8210"], "--reasoning"))
        self.assertTrue(qualifier.supported(HELP["b9014"], "--reasoning"))
        self.assertTrue(qualifier.supported(HELP["b8210"], "--reasoning-format"))


class QualifiedArgumentTests(unittest.TestCase):
    def test_draft_settings_are_spelled_for_each_runtime(self):
        expected = {
            "b8210": ["--draft-max", "3", "--cache-type-k-draft", "q4_0", "--cache-type-v-draft", "q4_0"],
            "b9014": ["--spec-draft-n-max", "3", "--spec-draft-type-k", "q4_0", "--spec-draft-type-v", "q4_0"],
        }
        for release, arguments in expected.items():
            with self.subTest(release=release):
                result, _ = run_with_help(HELP[release], EMPTY, ("3", "q4_0", "q4_0"))
                self.assertEqual(result, arguments)
                for flag in result[::2]:
                    self.assertTrue(qualifier.supported(HELP[release], flag))

    def test_macos_defaults_follow_runtime_support(self):
        b9014, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True)
        self.assertEqual(b9014, ["--ctx-checkpoints", "32", "--spec-type", "ngram-mod"])
        # b8210 would accept ngram-mod but turns speculation off for Qwen3.5.
        b8210, _ = run_with_help(HELP["b8210"], EMPTY, defaults=True)
        self.assertEqual(b8210, ["--ctx-checkpoints", "32"])

    def test_llama_reasoning_uses_the_b9014_switch_instead_of_the_format(self):
        # On the Mac mini M4, b9014 with ODS's old argv (--reasoning-format none,
        # --reasoning left at auto) logged "thinking = 1" and returned a reasoning
        # trace as content; with --reasoning off it still put "<think>\n\n</think>\n\n"
        # in every content. --reasoning off with the default format matched b8210.
        for value, expected in (("", "off"), ("off", "off"), ('"on"', "on"), ("auto", "auto")):
            with self.subTest(value=value):
                result, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, spec_default="none",
                                          reasoning=value, reasoning_format="none")
                self.assertEqual(result, ["--ctx-checkpoints", "32", "--reasoning", expected])
        # b8210 has no --reasoning switch: keep the caller's format mapping.
        b8210, _ = run_with_help(HELP["b8210"], EMPTY, defaults=True, reasoning="off", reasoning_format="none")
        self.assertEqual(b8210, ["--ctx-checkpoints", "32", "--reasoning-format", "none"])
        # A value --reasoning does not accept keeps the caller's format, even on b9014.
        other, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, spec_default="none",
                                 reasoning="deepseek", reasoning_format="deepseek")
        self.assertEqual(other, ["--ctx-checkpoints", "32", "--reasoning-format", "deepseek"])
        # Registered profiles (no defaults) keep their own argument list.
        with patch.object(qualifier.subprocess, "run") as run:
            self.assertEqual(qualifier.qualify("/runtime", EMPTY, reasoning="off", reasoning_format="none"), [])
            run.assert_not_called()
        with self.assertRaises(ValueError):
            qualifier.qualify("/runtime", EMPTY, defaults=True, reasoning="off", reasoning_format="none;x")

    def test_defaults_are_off_unless_requested(self):
        with patch.object(qualifier.subprocess, "run") as run:
            self.assertEqual(qualifier.qualify("/runtime", EMPTY), [])
            run.assert_not_called()

    def test_explicit_settings_and_opt_outs_win(self):
        cases = (
            (dict(spec_default="none"), ["--ctx-checkpoints", "32"]),
            (dict(spec_default='"none"'), ["--ctx-checkpoints", "32"]),
            (dict(spec_default="ngram-simple"), ["--ctx-checkpoints", "32", "--spec-type", "ngram-simple"]),
            # The caller passes LLAMA_ARG_SPEC_TYPE itself; no second --spec-type.
            (dict(spec_type="draft-mtp", spec_default="none"), ["--ctx-checkpoints", "32"]),
            (dict(spec_type="ngram-mod"), ["--ctx-checkpoints", "32"]),
        )
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                result, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, **kwargs)
                self.assertEqual(result, expected)
        for checkpoints in ("8", "0"):
            with self.subTest(checkpoints=checkpoints):
                result, _ = run_with_help(HELP["b9014"], ("", checkpoints, "", "", ""), defaults=True, spec_default="none")
                self.assertEqual(result, ["--ctx-checkpoints", checkpoints])

    def test_invalid_settings_fail_before_running_the_runtime(self):
        cases = (
            dict(draft=("0", "", "")), dict(draft=("257", "", "")), dict(draft=("3;4", "", "")),
            dict(draft=("$(touch x)", "", "")), dict(draft=("", "Q8 0", "")), dict(draft=("", "", "f16\nq8_0")),
            dict(spec_default="draft-mtp", defaults=True), dict(spec_default="bogus", defaults=True),
        )
        for kwargs in cases:
            with self.subTest(**kwargs), patch.object(qualifier.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    qualifier.qualify("/runtime", EMPTY, **kwargs)
                run.assert_not_called()

    def test_unsupported_explicit_draft_setting_is_rejected(self):
        with self.assertRaises(ValueError):
            run_with_help("--model N\n--spec-type [none|ngram-mod]\n", EMPTY, ("3", "", ""))

    def test_defaults_never_block_a_runtime_that_cannot_be_probed(self):
        for error in (OSError("missing"), subprocess.TimeoutExpired("runtime", 15), subprocess.CalledProcessError(1, "runtime")):
            with self.subTest(error=type(error).__name__), patch.object(qualifier.subprocess, "run", side_effect=error):
                self.assertEqual(qualifier.qualify("/runtime", EMPTY, defaults=True), [])
                # The caller's reasoning format still reaches an unprobed runtime.
                self.assertEqual(qualifier.qualify("/runtime", EMPTY, defaults=True, reasoning="",
                                                   reasoning_format="none"), ["--reasoning-format", "none"])
                with self.assertRaises((OSError, subprocess.SubprocessError)):
                    qualifier.qualify("/runtime", EMPTY, ("3", "", ""), defaults=True)

    @unittest.skipIf(os.name == "nt", "needs an executable shell script as the runtime")
    def test_cli_probes_the_runtime_and_emits_nul_framed_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "llama-server"
            cases = {
                "b8210": [b"--draft-max", b"2", b"--ctx-checkpoints", b"32", b"--reasoning-format", b"none"],
                "b9014": [b"--spec-draft-n-max", b"2", b"--ctx-checkpoints", b"32", b"--spec-type", b"ngram-mod",
                          b"--reasoning", b"off"],
            }
            for release, expected in cases.items():
                with self.subTest(release=release):
                    runtime.write_text(f"#!/bin/sh\n[ \"$1\" = --help ] && exec cat '{FIXTURES / (release + '.txt')}'\nexit 9\n")
                    runtime.chmod(0o755)
                    # The same argument list native-model.sh passes for a fresh .env.
                    result = subprocess.run(
                        [sys.executable, str(SOURCE), "--binary", str(runtime), "--interval=", "--checkpoints=",
                         "--cache-mib=", "--idle-seconds=", "--min-spacing=", "--explicit-spec-type=",
                         "--spec-default=", "--draft-n-max=2", "--draft-type-k=", "--draft-type-v=",
                         "--reasoning-mode=", "--reasoning-format-fallback=none", "--apply-defaults"],
                        capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.split(b"\0")[:-1], expected)
            result = subprocess.run([sys.executable, str(SOURCE), "--binary", str(runtime), "--draft-n-max=0"],
                                    capture_output=True, timeout=30)
            self.assertEqual((result.returncode, result.stdout), (1, b""))
            self.assertIn(b"tuning rejected", result.stderr)


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs bash and an executable shell script as the runtime")
class LauncherWiringTests(unittest.TestCase):
    """Run the real helper and launch code against a fake b8210/b9014 runtime."""

    ENV = "LLAMA_ARG_SPEC_DRAFT_N_MAX=3\nLLAMA_REASONING=off\n"
    EXPECTED = {
        "b8210": ["--draft-max", "3", "--ctx-checkpoints", "32", "--reasoning-format", "none"],
        "b9014": ["--spec-draft-n-max", "3", "--ctx-checkpoints", "32", "--spec-type", "ngram-mod", "--reasoning", "off"],
    }
    # Stub the OS effects of a native launch; print the llama-server argv NUL-framed.
    STUBS = r'''
read_env_value() { sed -n "s/^$2=//p" "$1" | head -1; }
ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_err() { echo "$*" >&2; }
sleep() { :; }; curl() { return 0; }
macos_bind_probe_host() { printf '%s' 127.0.0.1; }
bash() {
    if [[ "$1" == "$INSTALL/installers/macos/lib/native-llama-service.sh" && "$2" == start ]]; then
        printf '%s\n' "$$" > "$5"
        shift 5
        for arg in "$@"; do printf '%s\0' "$arg"; done
    else
        command bash "$@"
    fi
}
'''

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "install"
        (self.install / "installers/macos/lib").mkdir(parents=True)
        (self.install / "data/models").mkdir(parents=True)
        (self.install / "data/models/Qwen3.5-9B-Q4_K_M.gguf").write_bytes(b"GGUF-test")
        shutil.copy(SOURCE, self.install / "installers/macos/lib" / SOURCE.name)
        (self.install / ".env").write_text(self.ENV)
        self.runtime = self.root / "llama-server"

    def use_runtime(self, release):
        self.runtime.write_text(f"#!/bin/sh\n[ \"$1\" = --help ] && exec cat '{FIXTURES / (release + '.txt')}'\nexit 9\n")
        self.runtime.chmod(0o755)

    def run_bash(self, script):
        result = subprocess.run(["bash", "-c", script], capture_output=True, timeout=60,
                                env=dict(os.environ, INSTALL=str(self.install), RUNTIME=str(self.runtime),
                                         ROOT=str(ROOT), ODS_PYTHON_CMD=sys.executable))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return [part.decode() for part in result.stdout.split(b"\0")[:-1]]

    def assert_argv(self, argv, release, spec=True):
        """One reasoning decision, the defaults, and only flags this runtime accepts."""
        flags = [part for part in argv if part.startswith("--")]
        for flag in flags:
            self.assertTrue(qualifier.supported(HELP[release], flag), f"{flag} rejected by {release}: {argv}")
        self.assertEqual(argv[argv.index("--ctx-checkpoints") + 1], "32")
        if release == "b9014":
            self.assertEqual(argv[argv.index("--reasoning") + 1], "off")
            self.assertNotIn("--reasoning-format", argv)
            self.assertEqual(argv[argv.index("--spec-draft-n-max") + 1], "3")
            self.assertEqual(flags.count("--spec-type"), 1 if spec else 0)
            if spec:
                self.assertEqual(argv[argv.index("--spec-type") + 1], "ngram-mod")
        else:
            self.assertEqual(argv[argv.index("--reasoning-format") + 1], "none")
            self.assertNotIn("--reasoning", argv)
            self.assertEqual(argv[argv.index("--draft-max") + 1], "3")
            self.assertNotIn("--spec-type", argv)
        self.assertEqual(len(flags), len(set(flags)), f"repeated flag: {argv}")

    def test_native_model_helper(self):
        script = r'''
set -euo pipefail
read_env_value() { sed -n "s/^$2=//p" "$1" | head -1; }
source "$ROOT/installers/macos/lib/native-model.sh"
macos_resolve_checkpoint_args "$INSTALL" "$RUNTIME" none
for arg in ${MACOS_NATIVE_CHECKPOINT_ARGS[@]+"${MACOS_NATIVE_CHECKPOINT_ARGS[@]}"}; do printf '%s\0' "$arg"; done
'''
        for release, expected in self.EXPECTED.items():
            with self.subTest(release=release):
                self.use_runtime(release)
                self.assertEqual(self.run_bash(script), expected)
        # Without the helper, defaults are skipped but the format still arrives.
        (self.install / "installers/macos/lib" / SOURCE.name).unlink()
        (self.install / ".env").write_text("LLAMA_REASONING=off\n")
        self.assertEqual(self.run_bash(script), ["--reasoning-format", "none"])

    def test_ods_macos_start(self):
        script = (
            "set -euo pipefail\n"
            'INSTALL_DIR="$INSTALL"; LLAMA_SERVER_BIN="$RUNTIME"; LLAMA_SERVER_PID_FILE="$INSTALL/data/llama.pid"\n'
            'source "$ROOT/installers/macos/lib/native-model.sh"\n'
            + self.STUBS +
            'eval "$(awk \'/^start_native_llama\\(\\)/ {p=1} /^stop_native_llama\\(\\)/ {p=0} p\' "$ROOT/installers/macos/ods-macos.sh")"\n'
            'read_ods_env() { ENV_ODS_MODE=local; ENV_CTX_SIZE=8192; ENV_LLAMA_REASONING="$(read_env_value "$INSTALL/.env" LLAMA_REASONING)"; }\n'
            "macos_configure_llm_bridge_from_env() { :; }\n"
            "get_native_llama_status() { NATIVE_LLAMA_RUNNING=false; NATIVE_LLAMA_HEALTHY=false; NATIVE_LLAMA_PID=0; }\n"
            "stop_native_llama() { :; }\n"
            "start_native_llama true\n"
        )
        for release in ("b8210", "b9014"):
            with self.subTest(release=release):
                self.use_runtime(release)
                argv = self.run_bash(script)
                self.assertEqual(argv[argv.index("--model") + 1], str(self.install / "data/models/Qwen3.5-9B-Q4_K_M.gguf"))
                self.assert_argv(argv, release)
        (self.install / ".env").write_text(self.ENV + "LLAMA_SPEC_TYPE=none\n")
        self.assert_argv(self.run_bash(script), "b9014", spec=False)

    def test_installer_launch(self):
        source = (ROOT / "installers/macos/install-macos.sh").read_text(encoding="utf-8")
        block = section(source, "        # Read reasoning mode from .env", "        # Wait for health endpoint")
        script = (
            "set -euo pipefail\n"
            'INSTALL_DIR="$INSTALL"; LLAMA_SERVER_BIN="$RUNTIME"; LLAMA_SERVER_PID_FILE="$INSTALL/data/llama.pid"\n'
            'MODEL_FULL_PATH="$INSTALL/data/models/model.gguf"; MAX_CONTEXT=65536; MACOS_NATIVE_PROFILE=false\n'
            'source "$ROOT/installers/macos/lib/native-model.sh"\n'
            + self.STUBS +
            "_macos_stop_install_owned_native_llama() { :; }\n"
            + block
        )
        for release in ("b8210", "b9014"):
            with self.subTest(release=release):
                self.use_runtime(release)
                argv = self.run_bash(script)
                self.assertEqual(argv[argv.index("--model") + 1], str(self.install / "data/models/model.gguf"))
                self.assert_argv(argv, release)

    def test_bootstrap_full_model_swap(self):
        source = (ROOT / "scripts/bootstrap-upgrade.sh").read_text(encoding="utf-8")
        block = section(source, "            # Read reasoning mode from .env", "            # Capture old model path")
        script = (
            "set -uo pipefail\n"
            'ENV_FILE="$INSTALL/.env"; INSTALL_DIR="$INSTALL"; LLAMA_SERVER_BIN="$RUNTIME"\n'
            "log() { echo \"$*\" >&2; }\n"
            'read_env_value() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }\n'
            + block +
            '\nfor arg in ${_llama_tuning_args[@]+"${_llama_tuning_args[@]}"}; do printf "%s\\0" "$arg"; done\n'
        )
        for release, expected in self.EXPECTED.items():
            with self.subTest(release=release):
                self.use_runtime(release)
                self.assertEqual(self.run_bash(script), expected)
        # A rejected setting keeps the swap going with only the reasoning format.
        (self.install / ".env").write_text("LLAMA_ARG_SPEC_DRAFT_N_MAX=0\n")
        self.assertEqual(self.run_bash(script), ["--reasoning-format", "none"])


class LauncherContractTests(unittest.TestCase):
    def test_pinned_release_is_checksummed_and_has_ngram_mod(self):
        release = pinned_release()
        self.assertGreaterEqual(int(release[1:]), 8955, "the ngram-mod default needs llama.cpp b8955+")
        download = (ROOT / "installers/macos/lib/native-runtime-download.sh").read_text(encoding="utf-8")
        self.assertRegex(download, re.escape(release) + r"\) printf '%s\\n' '[0-9a-f]{64}' ;;")
        self.assertIn(release, HELP, f"add tests/fixtures/llama-server-help/{release}.txt for the new pin")
        self.assertTrue(qualifier.supported(HELP[release], qualifier.NGRAM_MOD_CAPABILITY))

    def launcher_flags(self):
        install = (ROOT / "installers/macos/install-macos.sh").read_text(encoding="utf-8")
        cli = (ROOT / "installers/macos/ods-macos.sh").read_text(encoding="utf-8")
        upgrade = (ROOT / "scripts/bootstrap-upgrade.sh").read_text(encoding="utf-8")
        agent_flags, qualified_keys = host_agent_flags()
        return {
            "install-macos.sh": array_flags(section(install, "# Read reasoning mode from .env", "# Wait for health endpoint"), "_llama_args"),
            "ods-macos.sh": array_flags(section(cli, "start_native_llama() {", "stop_native_llama() {"), "llama_args"),
            "bootstrap-upgrade.sh": array_flags(section(upgrade, "# macOS native llama-server (Metal)", "# Wait for health"), "_llama_args"),
            "ods-host-agent.py": agent_flags,
        }, qualified_keys

    def test_static_launcher_flags_work_on_every_pinned_runtime(self):
        launchers, _ = self.launcher_flags()
        for name, flags in launchers.items():
            self.assertIn("--model", flags, f"{name}: launcher block not found")
            for release, help_text in HELP.items():
                for flag in sorted(flags):
                    with self.subTest(launcher=name, release=release, flag=flag):
                        self.assertTrue(qualifier.supported(help_text, flag),
                                        f"{name} passes {flag}, which llama.cpp {release} rejects")

    def test_draft_flags_only_come_from_the_qualifier_on_macos(self):
        launchers, qualified_keys = self.launcher_flags()
        self.assertEqual(qualified_keys, {"LLAMA_ARG_SPEC_DRAFT_N_MAX", "LLAMA_ARG_SPEC_DRAFT_TYPE_K", "LLAMA_ARG_SPEC_DRAFT_TYPE_V"})
        for name, flags in launchers.items():
            self.assertFalse(flags & {"--spec-draft-n-max", "--draft-max", "--spec-draft-type-k", "--spec-draft-type-v"}, name)


if __name__ == "__main__":
    unittest.main()
