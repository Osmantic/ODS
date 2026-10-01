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
# With --apply-defaults the helper owns --parallel; LLAMA_PARALLEL unset is 1.
ONE_SLOT = ["--parallel", "1"]
# Catalog GGUFs by file name; the directory (e.g. an external model store) does not matter.
QWEN9B = "/Volumes/Models/Qwen3.5-9B-Q4_K_M.gguf"
GIB = 1024 ** 3
MIB = 1024 ** 2


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
        self.assertEqual(b9014, ONE_SLOT + ["--ctx-checkpoints", "32", "--spec-type", "ngram-mod"])
        # b8210 would accept ngram-mod but turns speculation off for Qwen3.5.
        b8210, _ = run_with_help(HELP["b8210"], EMPTY, defaults=True)
        self.assertEqual(b8210, ONE_SLOT + ["--ctx-checkpoints", "32"])

    def test_llama_reasoning_uses_the_b9014_switch_instead_of_the_format(self):
        # On the Mac mini M4, b9014 with ODS's old argv (--reasoning-format none,
        # --reasoning left at auto) logged "thinking = 1" and returned a reasoning
        # trace as content; with --reasoning off it still put "<think>\n\n</think>\n\n"
        # in every content. --reasoning off with the default format matched b8210.
        for value, expected in (("", "off"), ("off", "off"), ('"on"', "on"), ("auto", "auto")):
            with self.subTest(value=value):
                result, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, spec_default="none",
                                          reasoning=value, reasoning_format="none")
                self.assertEqual(result, ONE_SLOT + ["--ctx-checkpoints", "32", "--reasoning", expected])
        # b8210 has no --reasoning switch: keep the caller's format mapping.
        b8210, _ = run_with_help(HELP["b8210"], EMPTY, defaults=True, reasoning="off", reasoning_format="none")
        self.assertEqual(b8210, ONE_SLOT + ["--ctx-checkpoints", "32", "--reasoning-format", "none"])
        # A value --reasoning does not accept keeps the caller's format, even on b9014.
        other, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, spec_default="none",
                                 reasoning="deepseek", reasoning_format="deepseek")
        self.assertEqual(other, ONE_SLOT + ["--ctx-checkpoints", "32", "--reasoning-format", "deepseek"])
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
            (dict(spec_default="none"), ONE_SLOT + ["--ctx-checkpoints", "32"]),
            (dict(spec_default='"none"'), ONE_SLOT + ["--ctx-checkpoints", "32"]),
            (dict(spec_default="ngram-simple"), ONE_SLOT + ["--ctx-checkpoints", "32", "--spec-type", "ngram-simple"]),
            # The caller passes LLAMA_ARG_SPEC_TYPE itself; no second --spec-type.
            (dict(spec_type="draft-mtp", spec_default="none"), ONE_SLOT + ["--ctx-checkpoints", "32"]),
            (dict(spec_type="ngram-mod"), ONE_SLOT + ["--ctx-checkpoints", "32"]),
            (dict(parallel="4"), ["--parallel", "4", "--ctx-checkpoints", "32", "--spec-type", "ngram-mod"]),
            (dict(parallel='"2"'), ["--parallel", "2", "--ctx-checkpoints", "32", "--spec-type", "ngram-mod"]),
            (dict(parallel="-1"), ["--parallel", "-1", "--ctx-checkpoints", "32", "--spec-type", "ngram-mod"]),
        )
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                result, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, **kwargs)
                self.assertEqual(result, expected)
        for checkpoints in ("8", "0"):
            with self.subTest(checkpoints=checkpoints):
                result, _ = run_with_help(HELP["b9014"], ("", checkpoints, "", "", ""), defaults=True, spec_default="none")
                self.assertEqual(result, ONE_SLOT + ["--ctx-checkpoints", checkpoints])
        # Registered profiles pass their own --parallel; the helper adds none.
        result, _ = run_with_help(HELP["b9014"], EMPTY, ("3", "", ""), parallel="4")
        self.assertEqual(result, ["--spec-draft-n-max", "3"])

    def test_invalid_settings_fail_before_running_the_runtime(self):
        cases = (
            dict(draft=("0", "", "")), dict(draft=("257", "", "")), dict(draft=("3;4", "", "")),
            dict(draft=("$(touch x)", "", "")), dict(draft=("", "Q8 0", "")), dict(draft=("", "", "f16\nq8_0")),
            dict(spec_default="draft-mtp", defaults=True), dict(spec_default="bogus", defaults=True),
            dict(parallel="0", defaults=True), dict(parallel="-2", defaults=True),
            dict(parallel="257", defaults=True), dict(parallel="1.5", defaults=True),
            dict(parallel="2;3", defaults=True), dict(parallel="$(touch x)", defaults=True),
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
                # One slot (or LLAMA_PARALLEL) still reaches an unprobed runtime.
                self.assertEqual(qualifier.qualify("/runtime", EMPTY, defaults=True, model=QWEN9B), ONE_SLOT)
                self.assertEqual(qualifier.qualify("/runtime", EMPTY, defaults=True, parallel="3"), ["--parallel", "3"])
                # The caller's reasoning format still reaches an unprobed runtime.
                self.assertEqual(qualifier.qualify("/runtime", EMPTY, defaults=True, reasoning="",
                                                   reasoning_format="none"), ONE_SLOT + ["--reasoning-format", "none"])
                with self.assertRaises((OSError, subprocess.SubprocessError)):
                    qualifier.qualify("/runtime", EMPTY, ("3", "", ""), defaults=True)

    @unittest.skipIf(os.name == "nt", "needs an executable shell script as the runtime")
    def test_cli_probes_the_runtime_and_emits_nul_framed_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "llama-server"
            cases = {
                "b8210": [b"--parallel", b"1", b"--draft-max", b"2", b"--ctx-checkpoints", b"32",
                          b"--reasoning-format", b"none"],
                "b9014": [b"--parallel", b"1", b"--spec-draft-n-max", b"2", b"--ctx-checkpoints", b"32",
                          b"--spec-type", b"ngram-mod", b"--reasoning", b"off"],
            }
            for release, expected in cases.items():
                with self.subTest(release=release):
                    runtime.write_text(f"#!/bin/sh\n[ \"$1\" = --help ] && exec cat '{FIXTURES / (release + '.txt')}'\nexit 9\n")
                    runtime.chmod(0o755)
                    # The same argument list native-model.sh passes for a fresh .env
                    # (no model: the shared-slot layout is covered by SharedSlotLayoutTests).
                    result = subprocess.run(
                        [sys.executable, str(SOURCE), "--binary", str(runtime), "--interval=", "--checkpoints=",
                         "--cache-mib=", "--idle-seconds=", "--min-spacing=", "--parallel=", "--model=",
                         "--explicit-spec-type=", "--spec-default=", "--draft-n-max=2", "--draft-type-k=",
                         "--draft-type-v=", "--reasoning-mode=", "--reasoning-format-fallback=none",
                         "--apply-defaults"],
                        capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.split(b"\0")[:-1], expected)
            result = subprocess.run([sys.executable, str(SOURCE), "--binary", str(runtime), "--draft-n-max=0"],
                                    capture_output=True, timeout=30)
            self.assertEqual((result.returncode, result.stdout), (1, b""))
            self.assertIn(b"tuning rejected", result.stderr)


class SharedSlotLayoutTests(unittest.TestCase):
    """--parallel 2 --kv-unified --ctx-checkpoints 64 --cache-ram 0 on small Macs."""

    SHARED = ["--parallel", "2", "--kv-unified", "--ctx-checkpoints", "64", "--cache-ram", "0"]

    def qualify(self, host_bytes=16 * GIB, help_text=None, values=EMPTY, **kwargs):
        kwargs.setdefault("model", QWEN9B)
        kwargs.setdefault("spec_default", "none")
        with patch.object(qualifier, "host_memory_bytes", return_value=host_bytes):
            result, _ = run_with_help(HELP["b9014"] if help_text is None else help_text, values,
                                      defaults=True, **kwargs)
        return result

    def test_catalog_hybrid_model_on_a_16_gib_mac(self):
        self.assertEqual(self.qualify(), self.SHARED)
        # The rest of the defaults are unchanged around the layout.
        self.assertEqual(self.qualify(spec_default="", reasoning="", reasoning_format="none"),
                         self.SHARED + ["--spec-type", "ngram-mod", "--reasoning", "off"])
        self.assertEqual(self.qualify(8 * GIB, model="/models/Qwen3.5-2B-Q4_K_M.gguf"), self.SHARED)
        # Owner tuning that does not touch slots, checkpoints or the cache stays explicit.
        self.assertEqual(self.qualify(values=("", "", "", "120", ""), draft=("3", "", "")),
                         ["--parallel", "2", "--kv-unified", "--sleep-idle-seconds", "120",
                          "--spec-draft-n-max", "3", "--ctx-checkpoints", "64", "--cache-ram", "0"])

    def test_owner_settings_keep_the_one_slot_default(self):
        cases = (
            (dict(parallel="1"), ["--parallel", "1", "--ctx-checkpoints", "32"]),
            (dict(parallel="2"), ["--parallel", "2", "--ctx-checkpoints", "32"]),
            (dict(values=("", "48", "", "", "")), ONE_SLOT + ["--ctx-checkpoints", "48"]),
            (dict(values=("", "", "2048", "", "")), ONE_SLOT + ["--cache-ram", "2048", "--ctx-checkpoints", "32"]),
            (dict(values=("", "", "0", "", "")), ONE_SLOT + ["--cache-ram", "0", "--ctx-checkpoints", "32"]),
        )
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(self.qualify(**kwargs), expected)

    def test_only_measured_hardware_models_and_runtimes(self):
        legacy = ONE_SLOT + ["--ctx-checkpoints", "32"]
        cases = (
            # Larger Macs keep the RAM prompt cache; only 16 GiB was measured.
            dict(host_bytes=16 * GIB + 1), dict(host_bytes=18 * GIB), dict(host_bytes=64 * GIB),
            # b8210 has no --cache-idle-slots: not the measured runtime.
            dict(help_text=HELP["b8210"]),
            # No model, a GGUF the catalog does not know, and non-hybrid catalog models.
            dict(model=""), dict(model="/models/custom-hybrid.gguf"),
            dict(model="/models/gemma-4-E4B-it-Q4_K_M.gguf"),       # sliding window only
            dict(model="/models/Ministral-3-8B-Instruct-2512-Q4_K_M.gguf"),  # dense
            dict(model="/models/granite-4.0-h-tiny-Q4_K_M.gguf"),   # hybrid, layout not reviewed
            # Hybrid, but 128 checkpoints of 150 MiB exceed the one-slot budget.
            dict(model="/models/Qwen3.5-27B-Q4_K_M.gguf"),
        )
        for kwargs in cases:
            with self.subTest(**{k: v for k, v in kwargs.items() if k != "help_text"},
                              runtime="b8210" if "help_text" in kwargs else "b9014"):
                self.assertEqual(self.qualify(**kwargs), legacy)

    def test_budget_never_exceeds_the_one_slot_default(self):
        # Qwen3.5-9B (50.25 MiB per checkpoint): 6.4 GiB where one slot could reach 9.6.
        self.assertTrue(qualifier.shared_slot_budget_fits(16 * GIB, 52690944))
        self.assertTrue(qualifier.shared_slot_budget_fits(16 * GIB, 83 * MIB))
        self.assertFalse(qualifier.shared_slot_budget_fits(16 * GIB, 84 * MIB))
        for checkpoint_bytes in (83 * MIB, 52690944, 20201472):
            shared = (2 * 64 + 2) * checkpoint_bytes
            self.assertLessEqual(shared, 32 * checkpoint_bytes + 8192 * MIB)
        self.assertFalse(qualifier.shared_slot_budget_fits(16 * GIB, 0))
        self.assertFalse(qualifier.shared_slot_budget_fits(0, 52690944))

    def test_catalog_checkpoint_sizes(self):
        self.assertEqual(qualifier.model_checkpoint_bytes(QWEN9B), 52690944)
        self.assertEqual(qualifier.model_checkpoint_bytes("/x/NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf"), 85026816)
        for name in ("gemma-4-E4B-it-Q4_K_M.gguf", "phi-4-Q4_K_M.gguf", "unknown.gguf",
                     "granite-4.0-h-micro-Q4_K_M.gguf"):
            with self.subTest(name=name):
                self.assertEqual(qualifier.model_checkpoint_bytes("/x/" + name), 0)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(OSError):
                qualifier.model_checkpoint_bytes(QWEN9B, Path(temporary))

    def test_unreadable_host_or_catalog_keeps_one_slot(self):
        legacy = ONE_SLOT + ["--ctx-checkpoints", "32"]
        errors = (OSError("sysctl missing"), subprocess.CalledProcessError(1, "sysctl"),
                  subprocess.TimeoutExpired("sysctl", 5), ValueError("invalid hw.memsize"))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch.object(qualifier, "host_memory_bytes", side_effect=error), \
                    patch.object(qualifier.sys, "stderr") as stderr:
                result, _ = run_with_help(HELP["b9014"], EMPTY, defaults=True, spec_default="none", model=QWEN9B)
                self.assertEqual(result, legacy)
                self.assertIn("shared-slot default skipped", "".join(c.args[0] for c in stderr.write.call_args_list))
        # An install without the catalog or the shared memory model.
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(qualifier, "INSTALL_ROOT", Path(temporary)), \
                patch.object(qualifier.sys, "stderr"):
            self.assertEqual(self.qualify(), legacy)

    def test_host_memory_comes_from_sysctl_with_a_deadline(self):
        with patch.object(qualifier.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, "17179869184\n", "")) as run:
            self.assertEqual(qualifier.host_memory_bytes(), 16 * GIB)
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/sysctl", "-n", "hw.memsize"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        for output in ("0\n", "-1\n", "sixteen\n"):
            with self.subTest(output=output), patch.object(
                    qualifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaises(ValueError):
                    qualifier.host_memory_bytes()


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs bash and an executable shell script as the runtime")
class LauncherWiringTests(unittest.TestCase):
    """Run the real helper and launch code against a fake b8210/b9014 runtime."""

    ENV = "LLAMA_ARG_SPEC_DRAFT_N_MAX=3\nLLAMA_REASONING=off\n"
    # No catalog in this fake install, so the one-slot default applies.
    EXPECTED = {
        "b8210": ONE_SLOT + ["--draft-max", "3", "--ctx-checkpoints", "32", "--reasoning-format", "none"],
        "b9014": ONE_SLOT + ["--spec-draft-n-max", "3", "--ctx-checkpoints", "32", "--spec-type", "ngram-mod",
                             "--reasoning", "off"],
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
    NATIVE_MODEL_SCRIPT = r'''
set -euo pipefail
read_env_value() { sed -n "s/^$2=//p" "$1" | head -1; }
source "$ROOT/installers/macos/lib/native-model.sh"
macos_resolve_checkpoint_args "$INSTALL" "$RUNTIME" none "$INSTALL/data/models/Qwen3.5-9B-Q4_K_M.gguf"
for arg in ${MACOS_NATIVE_CHECKPOINT_ARGS[@]+"${MACOS_NATIVE_CHECKPOINT_ARGS[@]}"}; do printf '%s\0' "$arg"; done
'''
    # Stands in for the helper: records its argv and answers with the shared-slot layout.
    RECORDER = (
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[0]).with_name('helper-argv').write_text('\\0'.join(sys.argv[1:]))\n"
        "sys.stdout.buffer.write(b'--parallel\\0' + b'2\\0' + b'--kv-unified\\0')\n"
    )

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

    def ods_macos_script(self):
        return (
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

    def installer_script(self):
        source = (ROOT / "installers/macos/install-macos.sh").read_text(encoding="utf-8")
        block = section(source, "        # Read reasoning mode from .env", "        # Wait for health endpoint")
        return (
            "set -euo pipefail\n"
            'INSTALL_DIR="$INSTALL"; LLAMA_SERVER_BIN="$RUNTIME"; LLAMA_SERVER_PID_FILE="$INSTALL/data/llama.pid"\n'
            'MODEL_FULL_PATH="$INSTALL/data/models/model.gguf"; MAX_CONTEXT=65536; MACOS_NATIVE_PROFILE=false\n'
            'source "$ROOT/installers/macos/lib/native-model.sh"\n'
            + self.STUBS +
            "_macos_stop_install_owned_native_llama() { :; }\n"
            + block
        )

    def bootstrap_script(self):
        source = (ROOT / "scripts/bootstrap-upgrade.sh").read_text(encoding="utf-8")
        block = section(source, "            # Read reasoning mode from .env", "            # Capture old model path")
        return (
            "set -uo pipefail\n"
            'ENV_FILE="$INSTALL/.env"; INSTALL_DIR="$INSTALL"; LLAMA_SERVER_BIN="$RUNTIME"\n'
            '_model_path="$INSTALL/data/models/Qwen3.5-9B-Q4_K_M.gguf"\n'
            "log() { echo \"$*\" >&2; }\n"
            'read_env_value() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }\n'
            + block +
            '\nfor arg in ${_llama_tuning_args[@]+"${_llama_tuning_args[@]}"}; do printf "%s\\0" "$arg"; done\n'
        )

    def assert_argv(self, argv, release, spec=True):
        """One slot decision, one reasoning decision, the defaults, and only flags this runtime accepts."""
        flags = [part for part in argv if part.startswith("--")]
        for flag in flags:
            self.assertTrue(qualifier.supported(HELP[release], flag), f"{flag} rejected by {release}: {argv}")
        self.assertEqual(argv[argv.index("--parallel") + 1], "1")
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
        for release, expected in self.EXPECTED.items():
            with self.subTest(release=release):
                self.use_runtime(release)
                self.assertEqual(self.run_bash(self.NATIVE_MODEL_SCRIPT), expected)
        # Without the helper, defaults are skipped but the slot count and format still arrive.
        (self.install / "installers/macos/lib" / SOURCE.name).unlink()
        (self.install / ".env").write_text("LLAMA_REASONING=off\n")
        self.assertEqual(self.run_bash(self.NATIVE_MODEL_SCRIPT), ONE_SLOT + ["--reasoning-format", "none"])
        (self.install / ".env").write_text("LLAMA_REASONING=off\nLLAMA_PARALLEL=3\n")
        self.assertEqual(self.run_bash(self.NATIVE_MODEL_SCRIPT), ["--parallel", "3", "--reasoning-format", "none"])

    def test_ods_macos_start(self):
        for release in ("b8210", "b9014"):
            with self.subTest(release=release):
                self.use_runtime(release)
                argv = self.run_bash(self.ods_macos_script())
                self.assertEqual(argv[argv.index("--model") + 1], str(self.install / "data/models/Qwen3.5-9B-Q4_K_M.gguf"))
                self.assert_argv(argv, release)
        (self.install / ".env").write_text(self.ENV + "LLAMA_SPEC_TYPE=none\n")
        self.assert_argv(self.run_bash(self.ods_macos_script()), "b9014", spec=False)

    def test_installer_launch(self):
        for release in ("b8210", "b9014"):
            with self.subTest(release=release):
                self.use_runtime(release)
                argv = self.run_bash(self.installer_script())
                self.assertEqual(argv[argv.index("--model") + 1], str(self.install / "data/models/model.gguf"))
                self.assert_argv(argv, release)

    def test_bootstrap_full_model_swap(self):
        for release, expected in self.EXPECTED.items():
            with self.subTest(release=release):
                self.use_runtime(release)
                self.assertEqual(self.run_bash(self.bootstrap_script()), expected)
        # A rejected setting keeps the swap going with one slot and the reasoning format.
        (self.install / ".env").write_text("LLAMA_ARG_SPEC_DRAFT_N_MAX=0\n")
        self.assertEqual(self.run_bash(self.bootstrap_script()), ONE_SLOT + ["--reasoning-format", "none"])

    def test_launchers_leave_parallel_to_the_helper_and_pass_the_model(self):
        """The helper owns --parallel and needs the GGUF path to pick the shared-slot layout."""
        helper = self.install / "installers/macos/lib" / SOURCE.name
        helper.write_text(self.RECORDER)
        recorded = helper.with_name("helper-argv")
        self.use_runtime("b9014")
        models = self.install / "data/models"
        launchers = (
            ("native-model.sh", self.NATIVE_MODEL_SCRIPT, models / "Qwen3.5-9B-Q4_K_M.gguf"),
            ("ods-macos.sh", self.ods_macos_script(), models / "Qwen3.5-9B-Q4_K_M.gguf"),
            ("install-macos.sh", self.installer_script(), models / "model.gguf"),
            ("bootstrap-upgrade.sh", self.bootstrap_script(), models / "Qwen3.5-9B-Q4_K_M.gguf"),
        )
        for parallel in ("", "3"):
            (self.install / ".env").write_text(self.ENV + f"LLAMA_PARALLEL={parallel}\n")
            for name, script, model in launchers:
                with self.subTest(launcher=name, parallel=parallel):
                    recorded.unlink(missing_ok=True)
                    argv = self.run_bash(script)
                    helper_argv = recorded.read_text().split("\0")
                    self.assertIn(f"--model={model}", helper_argv)
                    self.assertIn(f"--parallel={parallel}", helper_argv)
                    self.assertIn("--apply-defaults", helper_argv)
                    self.assertEqual(argv.count("--parallel"), 1, argv)
                    self.assertEqual(argv[argv.index("--parallel") + 1], "2")
                    self.assertIn("--kv-unified", argv)


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

    def test_slot_count_only_comes_from_the_qualifier_on_macos(self):
        # The qualifier picks one slot or the shared-slot layout; a second
        # --parallel from a launcher would silently override its choice.
        launchers, _ = self.launcher_flags()
        for name in ("install-macos.sh", "ods-macos.sh", "bootstrap-upgrade.sh"):
            self.assertNotIn("--parallel", launchers[name], name)
            self.assertNotIn("--kv-unified", launchers[name], name)

    def test_draft_flags_only_come_from_the_qualifier_on_macos(self):
        launchers, qualified_keys = self.launcher_flags()
        self.assertEqual(qualified_keys, {"LLAMA_ARG_SPEC_DRAFT_N_MAX", "LLAMA_ARG_SPEC_DRAFT_TYPE_K", "LLAMA_ARG_SPEC_DRAFT_TYPE_V"})
        for name, flags in launchers.items():
            self.assertFalse(flags & {"--spec-draft-n-max", "--draft-max", "--spec-draft-type-k", "--spec-draft-type-v"}, name)


if __name__ == "__main__":
    unittest.main()
