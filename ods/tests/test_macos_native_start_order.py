import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'installers/macos/install-macos.sh'


EARLY_START = '    # ── Download and start native llama-server (Metal) ──'
EARLY_END = '    # ── Assemble Docker Compose flags'
LATE_START = '    if $ENABLE_PIXEL; then\n        ai "Preparing native Pixel'
LATE_END = '    # ── Launch background model upgrade'


def _extract(script, start_marker, end_marker):
    start = script.index(start_marker)
    end = script.index(end_marker, start)
    return script[start:end]


def _blocks(script=None):
    script = SOURCE.read_text() if script is None else script
    early = _extract(script, EARLY_START, EARLY_END)
    late = _extract(script, LATE_START, LATE_END)
    return early, late


def _harness(tmp_path, early, late, *, pixel_rc=0, native_rc=0, cloud=False,
             pixel_enabled=True, native_ready=False, health_rc=0, alive_rc=0, profile=False,
             model_path='/fake/model.gguf',
             max_context=65536, checkpoint_args=('--ctx-checkpoints', '32', '--cache-ram', '0', '--ubatch-size', '512')):
    events = tmp_path / 'events.log'
    lib = tmp_path / 'lib'
    lib.mkdir()
    stub = lib / 'pixel-native-install.py'
    stub.write_text(
        'import sys\n'
        'open(' + repr(str(events)) + ', "a").write("pixel\\n")\n'
        'sys.exit(' + str(pixel_rc) + ')\n'
    )
    native_stub = lib / 'native-llama-service.sh'
    native_stub.write_text(
        '#!/bin/bash\n'
        'echo $$ > "$4"\n'
        'printf "native\\n" >> ' + shlex.quote(str(events)) + '\n'
        'printf "%s\\n" "$@" > ' + shlex.quote(str(tmp_path / 'argv.txt')) + '\n'
        'cat "$2/.compose-flags" > ' + shlex.quote(str(tmp_path / 'flags-at-launch.txt')) + ' 2>/dev/null || true\n'
        'exit ' + str(native_rc) + '\n'
    )
    native_stub.chmod(0o755)
    install = tmp_path / 'install'
    (install / 'installers/macos/lib').mkdir(parents=True)
    (install / 'installers/macos/lib/native-llama-service.sh').write_text(native_stub.read_text())
    (install / 'installers/macos/lib/native-llama-service.sh').chmod(0o755)
    (install / '.env').write_text('LLAMA_ARG_CACHE_TYPE_K=q8_0\nLLAMA_ARG_CACHE_TYPE_V=q8_0\n')
    (install / 'data').mkdir()
    log = tmp_path / 'install.log'
    early = early.replace('/usr/bin/python3', shlex.quote(sys.executable))
    late = late.replace('/usr/bin/python3', shlex.quote(sys.executable))
    ckpt = ' '.join(shlex.quote(a) for a in checkpoint_args)
    prelude = f'''
set -euo pipefail
CLOUD_MODE={'true' if cloud else 'false'}
ENABLE_PIXEL={'true' if pixel_enabled else 'false'}
INSTALL_DIR={shlex.quote(str(install))}
LIB_DIR={shlex.quote(str(lib))}
ODS_LOG_FILE={shlex.quote(str(log))}
LLAMA_SERVER_BIN=/fake/bin
LLAMA_SERVER_PID_FILE={shlex.quote(str(tmp_path / 'llama.pid'))}
LLAMA_SERVER_LOG={shlex.quote(str(tmp_path / 'llama.log'))}
LLAMA_CPP_RELEASE_TAG=t
LLAMA_CPP_MACOS_ASSET=a
LLAMA_CPP_MACOS_URL=u
MAX_CONTEXT={max_context}
MACOS_NATIVE_BINARY=/fake/bin
MACOS_NATIVE_CONTEXT={max_context}
MACOS_NATIVE_MODEL_PATH={shlex.quote(model_path)}
MACOS_NATIVE_PROFILE={'true' if profile else 'false'}
MACOS_NATIVE_PROFILE_ARGS=()
MACOS_NATIVE_CHECKPOINT_ARGS=({ckpt})
MACOS_NATIVE_PROJECTOR_PATH=
MACOS_NATIVE_CHAT_TEMPLATE_PATH=
_MACOS_EXTERNAL_MODEL_READY={'true' if native_ready else 'false'}
GGUF_FILE=m
COMPOSE_FLAGS=(-f docker-compose.yml)
PIXEL_SOURCE_REF=
_pixel_install_args=()
chapter() {{ :; }}
ai() {{ :; }}
ai_ok() {{ :; }}
ai_err() {{ printf '%s\\n' "$*" >&2; }}
ai_warn() {{ :; }}
read_env_value() {{ echo ""; }}
macos_resolve_native_model() {{ :; }}
macos_install_native_llama() {{ :; }}
macos_bind_probe_host() {{ echo 127.0.0.1; }}
macos_resolve_checkpoint_args() {{ :; }}
_macos_stop_install_owned_native_llama() {{ printf 'stop\\n' >> {shlex.quote(str(events))}; }}
_macos_add_example_projects() {{ :; }}
sleep() {{ :; }}
curl() {{ return {health_rc}; }}
kill() {{ return {alive_rc}; }}
'''
    body = (prelude + early + '\nprintf "build\\n" >> ' + shlex.quote(str(events))
            + '\n' + late + '\nprintf "bootstrap\\n" >> ' + shlex.quote(str(events)) + '\n')
    result = subprocess.run(['/bin/bash', '-c', body], text=True, capture_output=True,
                            env={**os.environ}, timeout=15)
    return result, events


def _native_argv(events):
    return events.with_name('argv.txt').read_text().splitlines()


def test_native_start_after_pixel_success(tmp_path):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late)
    assert result.returncode == 0, result.stderr
    lines = events.read_text().splitlines()
    assert lines == ['stop', 'build', 'pixel', 'native', 'bootstrap']
    assert 'installers/macos/pixel-native.compose.yaml.disabled' in (tmp_path / 'flags-at-launch.txt').read_text()


def test_no_native_start_on_pixel_failure(tmp_path):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late, pixel_rc=1)
    assert result.returncode == 1
    lines = events.read_text().splitlines() if events.exists() else []
    assert lines == ['stop', 'build', 'pixel']


def test_no_native_start_when_cloud(tmp_path):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late, cloud=True)
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == ['build', 'pixel', 'bootstrap']


def test_native_still_starts_when_pixel_disabled(tmp_path):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late, pixel_enabled=False)
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == ['stop', 'build', 'native', 'bootstrap']


@pytest.mark.parametrize('model_path,max_context', [
    ('/fake/Qwen3.5-9B.gguf', 65536),
    ('/fake/model with space.gguf', 65536),
    ('/fake/Qwen3.5-2B-bootstrap.gguf', 32768),
])
def test_prepared_argv_preserved(tmp_path, model_path, max_context):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late, model_path=model_path,
                              max_context=max_context)
    assert result.returncode == 0, result.stderr
    argv = _native_argv(events)
    assert argv[0] == 'start'
    assert argv[1] == str(tmp_path / 'install')
    assert argv[2] == '/fake/bin'
    assert argv[3] == str(tmp_path / 'llama.pid')
    rest = argv[4:]
    expected = {'--model': model_path, '--ctx-size': str(max_context), '--n-gpu-layers': 'auto',
                '--cache-type-k': 'q8_0', '--cache-type-v': 'q8_0', '--ctx-checkpoints': '32',
                '--cache-ram': '0', '--ubatch-size': '512', '--parallel': '1'}
    for flag, value in expected.items():
        assert rest.count(flag) == 1
        assert rest[rest.index(flag) + 1] == value
    assert '--metrics' in rest


@pytest.mark.parametrize('native_rc,health_rc,alive_rc', [(1, 0, 0), (0, 1, 1)])
def test_native_failure_stops_before_bootstrap(tmp_path, native_rc, health_rc, alive_rc):
    early, late = _blocks()
    result, events = _harness(tmp_path, early, late, native_rc=native_rc, health_rc=health_rc, alive_rc=alive_rc)
    assert result.returncode != 0
    text = events.read_text()
    assert text.splitlines() == ['stop', 'build', 'pixel', 'native']
    assert 'installers/macos/pixel-native.compose.yaml.disabled' in (tmp_path / 'install/.compose-flags').read_text()


def test_prequalified_native_profile_still_launches_after_pixel(tmp_path):
    result, events = _harness(tmp_path, *_blocks(), profile=True, native_ready=True)
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == ['stop', 'build', 'pixel', 'native', 'bootstrap']
    argv = _native_argv(events)
    assert argv[argv.index('--reasoning-format') + 1] == 'none'


def test_model_launch_precedes_detached_bootstrap_dispatch():
    script = SOURCE.read_text()
    assert script.index('\n        _macos_start_prepared_native_llama\n') < script.index('    # ── Launch background model upgrade')
