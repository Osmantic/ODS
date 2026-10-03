"""Run the real installer verification helper with bounded service fixtures."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('args,accepted', [
    (['--confirm'], True),
    (['--confirm', '--ods-model-transaction', 'a' * 64], True),
    (['--confirm', '--ods-release-transaction', 'a' * 64], True),
    ([], False), (['--ods-model-transaction', 'a' * 64], False),
    (['--confirm', '--ods-model-transaction'], False),
    (['--confirm', '--ods-model-transaction', 'invalid'], False),
    (['--confirm', '--ods-model-transaction', 'a' * 64, '--skip-verify'], False),
    (['--confirm', '--skip-verify'], False),
])
def test_apply_internal_verification_forwards_only_exact_transaction(args, accepted):
    source = (ROOT / 'vendor/pixel/scripts/apply.sh').read_text()
    # Execute the actual argument parser and final verifier call, without
    # invoking the release's destructive service/deployment operations.
    parser = source[source.index('[[ ${1:-} == --confirm'):source.index('pixel_load_env')]
    verify = next(line for line in source.splitlines() if line.startswith('bash "$ROOT/scripts/verify.sh"'))
    script = 'set -eu\npixel_die() { exit 2; }\nROOT=/reviewed\n'
    script += parser + '\nbash() { printf "%s\\n" "$@"; }\n' + verify
    result = subprocess.run(['bash', '-c', script, 'fixture', *args], capture_output=True, text=True)
    assert (result.returncode == 0) == accepted, result.stderr
    if accepted:
        expected = [] if len(args) == 1 else ['--ods-model-transaction', args[2]]
        assert result.stdout.splitlines() == ['/reviewed/scripts/verify.sh', *expected]


@pytest.mark.parametrize('scenario', ['normal', 'prepare-failed', 'publish-failed', 'wrong-hash'])
def test_release_apply_keeps_configuration_publication_coordinated(tmp_path, scenario):
    source = (ROOT / 'vendor/pixel/scripts/apply.sh').read_text()
    prepare = source.split('if [[ -n "$ods_release_transaction" ]]; then', 1)[1].split('live_mutation_started=1', 1)[0]
    prepare = 'if [[ -n "$ods_release_transaction" ]]; then' + prepare
    publish = source.split('if [[ $ods_release_prepared == 1 ]]; then\n  published_access=', 1)[1].split(
        'install -m 600 "$ROOT/.generated/gateway.env"', 1)[0]
    publish = 'if [[ $ods_release_prepared == 1 ]]; then\n  published_access=' + publish
    events = tmp_path / 'events'
    candidate = tmp_path / 'candidate'
    candidate.write_text('{}')
    script = r'''
set -eu
ROOT=/reviewed
candidate=$1
events=$2
scenario=$3
ods_release_transaction=$(printf 'a%.0s' {1..64})
ods_release_before=''
ods_release_after=''
ods_release_prepared=0
pixel_die() { exit 2; }
install() { echo forbidden-direct-install >> "$events"; exit 3; }
python3() {
  echo "$3" >> "$events"
  case "$3" in
    prepare)
      [[ "$scenario" != prepare-failed ]] || return 1
      printf '%s %s\n' "$ods_release_transaction" "$(printf 'b%.0s' {1..64})";;
    publish)
      [[ "$scenario" != publish-failed ]] || return 1
      if [[ "$scenario" == wrong-hash ]]; then echo "$ods_release_transaction"
      else printf 'b%.0s' {1..64}; echo; fi;;
  esac
}
'''
    script += prepare + '\necho stopped >> "$events"\n' + publish + '\necho restart >> "$events"\n'
    result = subprocess.run(['bash', '-c', script, 'fixture', str(candidate), str(events), scenario],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (scenario == 'normal'), result.stderr
    expected = ['prepare'] if scenario == 'prepare-failed' else ['prepare', 'stopped', 'publish']
    if scenario == 'normal':
        expected.append('restart')
    assert events.read_text().splitlines() == expected


@pytest.mark.parametrize('failed', [False, True])
def test_release_rollback_never_overwrites_config_without_its_receipt(tmp_path, failed):
    source = (ROOT / 'vendor/pixel/scripts/apply.sh').read_text()
    restore = '  access_restore_failed=0\n' + source.split('  access_restore_failed=0\n', 1)[1].split(
        '  if [[ -n "$previous_target" ]]; then', 1)[0]
    events = tmp_path / 'events'
    script = r'''
set -eu
ROOT=/reviewed
events=$1
failed=$2
ods_release_transaction=$(printf 'a%.0s' {1..64})
ods_release_before=$(printf 'b%.0s' {1..64})
ods_release_prepared=1
python3() {
    echo "$3 $5" >> "$events"
    [[ "$failed" == False ]] || return 1
    echo "$ods_release_before"
}
install() { echo forbidden-direct-install >> "$events"; exit 3; }
'''
    script += restore + '\necho "$access_restore_failed"\n'
    result = subprocess.run(['bash', '-c', script, 'fixture', str(events), str(failed)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(int(failed))
    assert events.read_text().splitlines() == ['publish rollback']


@pytest.mark.parametrize('case,success', [
    ('sandboxed', True), ('full', True), ('borrowed', True), ('begin-fails', False),
    ('invalid-id', False), ('verify-fails', False), ('finish-fails', False), ('retry', True),
])
def test_verification_lifecycle(tmp_path, case, success):
    log = tmp_path / 'events'
    script = r'''
set -eu
source "$1/installers/lib/pixel-host-install.sh"
scenario=$2
events=$3
transaction=$(printf 'a%.0s' {1..64})
systemctl() {
    if [[ "$scenario" == sandboxed ]]; then
        case "$*" in
            *ProtectHome*) echo tmpfs;;
            *ProtectSystem*) echo strict;;
            *) return 1;;
        esac
    else echo no; fi
}
_ods_pixel_openclaw_bin() { echo /fixture/openclaw; }
_ods_pixel_install_access_service() { echo controller >> "$events"; }
_ods_pixel_model_transition() {
    echo "$*" >> "$events"
    if [[ "$1" == begin ]]; then
        [[ "$scenario" != begin-fails ]] || return 1
        if [[ "$scenario" == invalid-id ]]; then echo invalid; else echo "$transaction"; fi
    else
        [[ "$scenario" != finish-fails ]]
    fi
}
attempt=0
ods_pixel_run_as_owner() {
    echo "$*" >> "$events"
    attempt=$((attempt + 1))
    [[ "$scenario" != verify-fails ]] || return 1
    [[ "$scenario" != retry || "$attempt" -gt 1 ]]
}
sleep() { :; }
borrowed=''
[[ "$scenario" != borrowed ]] || borrowed=$transaction
_ods_pixel_verify_current_runtime fixture /fixture /pixel "$borrowed"
'''
    result = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), case, str(log)],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == success, result.stderr
    lines = log.read_text().splitlines()
    verification = [line for line in lines if '/pixel/pixel verify' in line]
    finishes = [line for line in lines if line.startswith('finish ')]
    if case == 'sandboxed':
        assert lines == ['fixture /fixture /pixel/pixel verify']
    elif case == 'borrowed':
        assert len(lines) == 1 and '--ods-model-transaction ' + 'a' * 64 in lines[0]
    elif case in ('begin-fails', 'invalid-id'):
        assert not verification and not finishes
    else:
        assert lines[0] == 'controller' and lines[1] == 'begin fixture /fixture'
        assert all('--ods-model-transaction ' + 'a' * 64 in line for line in verification)
        if case == 'verify-fails':
            assert len(verification) == 3 and not finishes
            assert 'admission remains held' in result.stderr
        else:
            assert len(verification) == (2 if case == 'retry' else 1)
            assert finishes == ['finish fixture /fixture ' + 'a' * 64 + ' applied']


def test_all_vendor_verify_calls_use_shared_lifecycle():
    source = (ROOT / 'installers/lib/pixel-host-install.sh').read_text()
    assert source.count('"$pixel_root/pixel" verify') == 1
    helper = source.split('_ods_pixel_verify_current_runtime() {', 1)[1].split(
        '_ods_pixel_restart_gateway_and_verify() {', 1)[0]
    assert '"$pixel_root/pixel" verify' in helper


@pytest.mark.parametrize('scenario', ['first', 'sandboxed', 'full', 'pending', 'bad-token'])
def test_release_begin_preserves_first_install_and_existing_holds(tmp_path, scenario):
    home = tmp_path / 'home'
    (home / '.openclaw').mkdir(parents=True)
    if scenario != 'first':
        (home / '.openclaw/openclaw.json').write_text('{}')
    script = r'''
set -eu
source "$1/installers/lib/pixel-host-install.sh"
scenario=$2
systemctl() {
    if [[ "$scenario" == sandboxed ]]; then
        case "$*" in *ProtectHome*) echo tmpfs;; *) echo strict;; esac
    else echo no; fi
}
_ods_pixel_openclaw_bin() { echo /fixture/openclaw; }
_ods_pixel_install_access_service() { :; }
_ods_pixel_model_transition() {
    [[ "$1" == begin ]] || return 9
    [[ "$scenario" != pending ]] || return 1
    if [[ "$scenario" == bad-token ]]; then echo invalid
    else printf 'a%.0s' {1..64}; echo; fi
}
_ods_pixel_begin_release_transition fixture "$3"
'''
    result = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), scenario, str(home)],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (scenario not in ('pending', 'bad-token'))
    assert result.stdout.strip() == ('a' * 64 if scenario == 'full' else '')


@pytest.mark.parametrize('fail_proof', [False, True, 'proof-once', 'release-once', 'release-always'])
def test_release_finish_releases_admission_only_after_exact_proof(tmp_path, fail_proof):
    events = tmp_path / 'events'
    script = r'''
set -eu
source "$1/installers/lib/pixel-host-install.sh"
events=$2
fail_proof=$3
proof_count=0
release_count=0
sleep() { :; }
ods_pixel_run_as_owner() {
    shift 2
    if [[ "$1" == sha256sum ]]; then printf 'b%.0s' {1..64}; echo '  config'; return; fi
    echo "$*" >> "$events"
    proof_count=$((proof_count + 1))
    [[ "$fail_proof" != True ]] || return 1
    [[ "$fail_proof" != proof-once || "$proof_count" -gt 1 ]]
}
_ods_pixel_model_transition() {
    echo "$*" >> "$events"
    release_count=$((release_count + 1))
    [[ "$fail_proof" != release-always ]] || return 1
    [[ "$fail_proof" != release-once || "$release_count" -gt 1 ]]
}
token=$(printf 'a%.0s' {1..64})
_ods_pixel_finish_release_transition fixture /home/fixture /pixel "$token"
'''
    result = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), str(events), str(fail_proof)],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (fail_proof not in (True, 'release-always')), result.stderr
    lines = events.read_text().splitlines()
    assert lines[0] == 'python3 -I /pixel/scripts/lib/ods-release-access.py finish ' + 'a' * 64 + ' ' + 'b' * 64 + ' apply'
    proofs = [line for line in lines if line.startswith('python3 ')]
    releases = [line for line in lines if line.startswith('finish ')]
    assert len(proofs) == (3 if fail_proof is True else 2 if fail_proof == 'proof-once' else 1)
    assert len(releases) == (0 if fail_proof is True else 3 if fail_proof == 'release-always'
                             else 2 if fail_proof == 'release-once' else 1)
    assert len(set(proofs)) == 1
    assert all(line == 'finish fixture /home/fixture ' + 'a' * 64 + ' applied' for line in releases)
    assert lines == proofs + releases


@pytest.mark.parametrize("scenario", ["initial", "generated", "ready", "active", "foreign", "extra"])
def test_access_reproof_distinguishes_empty_bootstrap(tmp_path, scenario):
    import json
    source = (ROOT / "installers/lib/pixel-host-install.sh").read_text()
    start = source.index("_ods_pixel_reprove_access_marker_if_needed() {")
    helper = source[start:source.index("\n_ods_pixel_install_access_service() {", start)]
    marker = tmp_path / ".config/ods/pixel-managed.json"
    marker.parent.mkdir(parents=True)
    value = dict(schema_version=2, manager="ods", state="installing",
                 initial_active_state="absent", install_dir=str(tmp_path), pixel_source_ref="a" * 40)
    if scenario == "ready":
        value["state"] = "ready"
    if scenario == "foreign":
        value["install_dir"] = "/another-install"
    if scenario == "extra":
        value["configuration_sha256"] = "b" * 64
    if scenario == "active":
        current = tmp_path / ".local/share/pixel/current"
        current.parent.mkdir(parents=True)
        current.symlink_to(current.parent / "missing-release")
    if scenario == "generated":
        config = tmp_path / ".openclaw/openclaw.json"
        config.parent.mkdir()
        config.write_text('{}')
    marker.write_text(json.dumps(value))
    script = r'''
set -eu
INSTALL_DIR=$1
ods_pixel_run_as_owner() { shift 2; "$@"; }
_ods_pixel_install_access_service() { echo unexpected-root-action; return 9; }
''' + helper + '\n_ods_pixel_reprove_access_marker_if_needed owner "$1" ""\n'
    result = subprocess.run(["bash", "-c", script, "test", str(tmp_path)],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (scenario == "initial"), result.stderr
    assert ("unexpected-root-action" in result.stdout) == (scenario == "generated")


def test_access_reproof_precedes_config_generating_bootstrap():
    source = (ROOT / "installers/lib/pixel-host-install.sh").read_text()
    install = source[source.index("ods_pixel_install_default_agent() {"):]
    reproof = "_ods_pixel_reprove_access_marker_if_needed \"$owner\" \"$home\""
    bootstrap = '"$pixel_root/pixel" bootstrap --apply'
    assert install.count(reproof) == 1
    assert install.index(reproof) < install.index(bootstrap)


@pytest.mark.parametrize("scenario", ["configured-new", "active-link", "attestation", "loaded"])
def test_initial_generated_config_needs_no_release_migration(tmp_path, scenario):
    source = (ROOT / "installers/lib/pixel-host-install.sh").read_text()
    start = source.index("_ods_pixel_begin_release_transition() {")
    helper = source[start:source.index("\n_ods_pixel_finish_release_transition()", start)]
    config = tmp_path / ".openclaw/openclaw.json"
    config.parent.mkdir()
    config.write_text("{}")
    runtime = tmp_path / ".local/share/pixel"
    runtime.mkdir(parents=True)
    if scenario == "active-link":
        (runtime / "current").symlink_to(runtime / "missing")
    if scenario == "attestation":
        (runtime / "runtime-attestation.json").write_text("{}")
    script = r'''
set -eu
scenario=$2
systemctl() {
  if [[ "$*" == *LoadState* ]]; then
    if [[ "$scenario" == loaded ]]; then echo loaded; else echo not-found; fi
  else echo false; fi
}
_ods_pixel_openclaw_bin() { echo /fake; }
_ods_pixel_install_access_service() { echo coordinator-required >&2; return 8; }
''' + helper + '\n_ods_pixel_begin_release_transition owner "$1"\n'
    result = subprocess.run(["bash", "-c", script, "test", str(tmp_path), scenario],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == (scenario == "configured-new"), result.stderr
    assert ("coordinator-required" in result.stderr) == (scenario != "configured-new")
