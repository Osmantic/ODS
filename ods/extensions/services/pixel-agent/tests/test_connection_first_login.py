"""First Google login must satisfy the real exchange unit's required env file.

Systemd and Google are simulated here; Root custody, the broker exchange and
handoff/credential lifecycle use the production code in disposable state.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX connection custody runs under Linux/WSL')

from pathlib import Path

import test_connection_coordinator as harness
from pixel_connections import coordinator as c, custody

world = harness.world


def test_first_login_supplies_mandatory_disabled_env_before_google_exchange(world, monkeypatch):
    original = c.RUN
    exchanges = []
    generated = Path(__file__).resolve().parents[4] / 'vendor/pixel/scripts/configure.mjs'
    source = generated.read_text()
    unit = source.split('const sourceExchangeService = `[Unit]', 1)[1].split('`;', 1)[0]
    assert 'EnvironmentFile=${systemdPath(values.PIXEL_SOURCE_BROKER_ENV)}' in unit

    def systemd(arguments, timeout):
        if arguments[:2] == ['systemctl', 'start'] and arguments[-1].startswith('pixel-source-exchange@'):
            # The real systemd unit refuses to exec broker.py without this file.
            assert world.paths.env.is_file()
            values = dict(custody.parse_env(world.paths.env.read_text()))
            assert all(values[key] == '0' for key in custody.PREAUTH_FLAGS)
            assert not world.paths.token.exists() and not world.google.calls
            exchanges.append(True)
        return original(arguments, timeout)

    monkeypatch.setattr(c, 'RUN', systemd)
    assert not world.paths.env.exists()
    result = harness.connect(world)
    assert result['outcome'] == 'connected'
    assert exchanges == [True] and len(world.google.calls) == 1
    values = dict(custody.parse_env(world.paths.env.read_text()))
    assert values['PIXEL_LIMB_EMAIL_ENABLED'] == values['PIXEL_LIMB_CALENDAR_ENABLED'] == '1'
    assert values['PIXEL_CALENDAR_DIRECT_ENABLED'] == '0'
    assert world.paths.token.is_file()
