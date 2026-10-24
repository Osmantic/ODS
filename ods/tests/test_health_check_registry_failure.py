"""An unavailable registry must never turn an incomplete check into healthy."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["no_yaml", "parser", "empty", "missing_library"])
@pytest.mark.parametrize("mode", ["human", "json", "quiet"])
def test_unavailable_registry_is_critical_before_any_probe(tmp_path, failure, mode):
    source = tmp_path / "source"
    install = tmp_path / "install"
    bin_dir = tmp_path / "bin"
    modules = tmp_path / "modules"
    for directory in (source / "scripts", source / "lib", install, bin_dir, modules):
        directory.mkdir(parents=True)
    shutil.copyfile(
        os.environ.get("HC_SOURCE_OVERRIDE", ROOT / "scripts/health-check.sh"),
        source / "scripts/health-check.sh",
    )
    # Use the real loader and interpreter selector, not a mock of _SR_FAILED.
    for name in ("service-registry.sh", "python-cmd.sh", "safe-env.sh"):
        shutil.copyfile(ROOT / "lib" / name, source / "lib" / name)
    if failure == "missing_library":
        (source / "lib/service-registry.sh").unlink()
    extensions = source / "extensions/services"
    if failure == "empty":
        extensions.mkdir(parents=True)
    else:
        extensions.parent.mkdir()
        extensions.symlink_to(ROOT / "extensions/services", target_is_directory=True)

    if failure == "no_yaml":
        # Shadow PyYAML for every interpreter the real selector can choose.
        (modules / "yaml.py").write_text("raise ModuleNotFoundError('No module named yaml')\n")
    elif failure == "parser":
        # Dependency probes work, but the actual registry subprocess fails.
        (modules / "yaml.py").write_text(
            "import sys\n"
            "if len(sys.argv) > 1 and sys.argv[1].endswith('/extensions/services'):\n"
            "    raise SystemExit('simulated manifest parser failure')\n"
        )

    # Even the unfixed script is confined to the fixture. Any attempted probe
    # is recorded; no Docker daemon, system service or network is contacted.
    probe = '''#!/bin/sh
echo "$0" >> "$PROBE_LOG"
case "$0" in
    */resolve-compose-stack.sh) echo '{"fragments":["docker-compose.base.yml"]}' ;;
    */curl) echo '{"choices":[{"text":"Hi"}]}' ;;
    */df) printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfixture 100 20 80 20%% /\n' ;;
    *) exit 1 ;;
esac
'''
    for name in ("docker", "curl", "nvidia-smi", "df"):
        path = bin_dir / name
        path.write_text(probe)
        path.chmod(0o755)
    resolver = source / "scripts/resolve-compose-stack.sh"
    resolver.write_text(probe)
    resolver.chmod(0o755)
    log = tmp_path / "probes"
    env = dict(os.environ, INSTALL_DIR=str(install), ODS_HOME=str(install),
               PYTHONPATH=str(modules), ODS_SR_AUTO_INSTALL_PYYAML="0",
               ODS_PYTHON_PREFER_SYSTEM="0", PROBE_LOG=str(log),
               PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
    args = ["bash", str(source / "scripts/health-check.sh")]
    if mode != "human":
        args.append("--" + mode)
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)

    assert result.returncode == 2, result
    assert "service health is unknown" in result.stderr
    assert "Re-run the installer" in result.stderr
    assert "Python/PyYAML" in result.stderr
    assert "unbound variable" not in result.stderr
    assert not log.exists(), "Probes must not run with an incomplete registry"
    if mode == "json":
        document = json.loads(result.stdout)
        assert document["status"] == "critical"
        assert document["services"] == {"service_registry": "fail"}
        assert document["timestamp"]
    elif mode == "quiet":
        assert result.stdout == ""
    else:
        assert "Status: CRITICAL - Service registry unavailable" in result.stdout
