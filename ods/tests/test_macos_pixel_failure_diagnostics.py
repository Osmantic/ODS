"""Public failure diagnostics disclose code locations, never exception payloads."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from test_macos_pixel_migration_selection import module
from pixel_access_bridge import AccessError
from pixel_connections.custody import CustodyError as SourceCustodyError
from pixel_macos_custody import CustodyError as MacCustodyError, protected_bytes


PRIVATE = 'PRIVATE_CREDENTIAL_SENTINEL'


def captured(call):
    try:
        call()
    except Exception as error:
        return error
    raise AssertionError('expected fixture exception')


@pytest.mark.parametrize('error,category', [
    (AccessError(PRIVATE), 'pixel_access_bridge.AccessError'),
    (MacCustodyError(PRIVATE), 'pixel_macos_custody.CustodyError'),
    (SourceCustodyError(PRIVATE), 'pixel_connections.custody.CustodyError'),
    (module._upgrade.UpgradeError(PRIVATE), 'UpgradeError'),
    (module._bundle.BundleError(PRIVATE), 'BundleError'),
    (OSError(13, PRIVATE, '/private/' + PRIVATE), 'OSError'),
    (module.subprocess.CalledProcessError(1, [PRIVATE], output=PRIVATE, stderr=PRIVATE), 'SubprocessError'),
])
def test_diagnostic_categories_never_include_exception_data(error, category):
    assert module._native_failure_diagnostic(error) == (category, None)


def test_deepest_real_custody_frame_survives_wrapped_private_messages():
    inner = captured(lambda: protected_bytes('relative/' + PRIVATE))
    inner.args = (PRIVATE,)
    outer = module._upgrade.UpgradeError(PRIVATE)
    outer.__cause__ = inner
    detail, guidance = module._native_failure_diagnostic(outer)
    assert detail.startswith('pixel_macos_custody.CustodyError at bin/pixel_macos_custody.py:')
    assert int(detail.rsplit(':', 1)[1]) > 0 and guidance is None
    assert PRIVATE not in detail and str(module.HERE.parents[2]) not in detail


def test_source_custody_category_uses_its_real_source_frame(monkeypatch):
    from pixel_connections import custody
    def private_failure():
        raise SourceCustodyError(PRIVATE)
    monkeypatch.setattr(custody, '_native_acl_library', private_failure)
    error = captured(lambda: custody._native_no_acl(-1))
    detail, guidance = module._native_failure_diagnostic(error)
    assert detail.startswith('pixel_connections.custody.CustodyError at bin/pixel_connections/custody.py:')
    assert guidance is None and PRIVATE not in detail


@pytest.mark.parametrize('spoof', [False, True])
def test_foreign_or_unbound_source_filenames_are_ignored(spoof):
    private_file = '/private/' + PRIVATE + '/payload.py'
    filename = str(module.HERE.parents[2] / 'bin/pixel_macos_custody.py') if spoof else private_file
    code = compile('raise failure', filename, 'exec')
    error = captured(lambda: exec(code, {'failure': MacCustodyError(PRIVATE), '__file__': private_file}))
    assert module._native_failure_diagnostic(error) == ('pixel_macos_custody.CustodyError', None)


def test_chain_and_cycles_are_bounded_without_calling_error_string():
    class PrivateError(ValueError):
        def __str__(self):
            raise AssertionError('exception string must not be inspected')
    first = current = PrivateError(PRIVATE)
    for _ in range(20):
        next_error = PrivateError(PRIVATE)
        current.__cause__ = next_error
        current = next_error
    current.__cause__ = first
    assert module._native_failure_diagnostic(first) == ('ValueError', None)
    first.__cause__ = first
    assert module._native_failure_diagnostic(first) == ('ValueError', None)
    assert module._native_failure_diagnostic(module.InstallError([PRIVATE])) == ('InstallError', None)


@pytest.mark.parametrize('operation', ['recovery', 'migration'])
def test_cli_reports_only_sanitized_wrapped_failure(monkeypatch, capsys, operation):
    inner = captured(lambda: protected_bytes('relative/' + PRIVATE))
    inner.args = (PRIVATE,)
    def fail(*_args, **_kwargs):
        raise module._upgrade.UpgradeError(PRIVATE) from inner
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    if operation == 'recovery':
        monkeypatch.setattr(module, '_recovery_bridge', lambda **_kwargs: object())
        monkeypatch.setattr(module, 'recover_install', fail)
        result = module._recovery_main(['--owner', PRIVATE, '--current-bundle-digest', 'a' * 64,
                                       '--bundle-digest', 'b' * 64])
        expected = 'native-runtime-recovery-failed'
    else:
        monkeypatch.setattr(module, 'make_migration_plan', fail)
        argv = []
        for name in ('source', 'install-dir', 'owner', 'candidate', 'runtime-bundle', 'bundle-digest',
                     'current-bundle-digest', 'services-bundle', 'services-digest', 'pixel-source-ref'):
            argv.extend(['--' + name, PRIVATE])
        result = module._migration_main(argv)
        expected = 'native-joint-migration-failed'
    output = capsys.readouterr()
    assert result == 1 and output.out == ''
    assert expected in output.err
    assert 'pixel_macos_custody.CustodyError at bin/pixel_macos_custody.py:' in output.err
    assert PRIVATE not in output.err and str(module.HERE.parents[2]) not in output.err
    assert str(Path(__file__)) not in output.err


def test_recovery_cli_passes_explicit_empty_stage_selection(monkeypatch, capsys):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, '_recovery_bridge', lambda **_kwargs: object())
    calls = []
    def recover(_bridge, **kwargs):
        calls.append(kwargs)
        kwargs['on_reproved']()
        return 'active'
    monkeypatch.setattr(module, 'recover_install', recover)
    result = module._recovery_main(['--owner', 'owner', '--current-bundle-digest', 'a' * 64,
        '--bundle-digest', 'b' * 64, '--quarantine-empty-source-stage'])
    output = capsys.readouterr()
    assert result == 0 and json.loads(output.out)['status'] == 'active' and output.err == ''
    assert calls[0]['quarantine_empty_source_stage'] is True
