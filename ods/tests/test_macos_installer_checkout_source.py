"""The separate source checkout must reach native provenance selection."""
import os
from pathlib import Path
import subprocess


HELPER = Path(__file__).resolve().parents[1] / 'installers/macos/lib/checkout-source.sh'


def selected(root, inherited=None):
    env = os.environ.copy()
    env.pop('ODS_BOOTSTRAP_SOURCE_DIR', None)
    if inherited is not None:
        env['ODS_BOOTSTRAP_SOURCE_DIR'] = inherited
    return subprocess.run(['/bin/bash', '-c',
        'source "$1"; ods_macos_select_checkout_source "$2"; '
        'if [[ ${ODS_BOOTSTRAP_SOURCE_DIR+x} ]]; then printf "set:%s" "$ODS_BOOTSTRAP_SOURCE_DIR"; else printf unset; fi',
        'checkout-source-test', str(HELPER), str(root)], env=env, check=True, capture_output=True, text=True).stdout


def test_checkout_is_forwarded(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    source = tmp_path / 'ods'
    source.mkdir()
    assert selected(source) == 'set:' + str(source)


def test_existing_bootstrap_pin_is_not_overwritten(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    assert selected(tmp_path, '/already/pinned') == 'set:/already/pinned'
    assert selected(tmp_path, '') == 'set:'


def test_installed_tree_without_git_is_not_claimed_as_checkout(tmp_path):
    assert selected(tmp_path) == 'unset'
