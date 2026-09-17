"""Contract tests for bin/ods-mdns.py record construction and env parsing.

`test_mdns_subdomains.py` pins the published subdomain set statically and
`test_mdns_refresh_recovery.py` pins republish-after-failure. These tests pin
the remaining runtime surface: .env parsing, port/bounds tolerance, the
loopback-only rule for direct-port SRV records, per-record fields, the
hostname validator, and the config signature that decides re-announcement.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MDNS = Path(__file__).resolve().parents[1] / 'bin' / 'ods-mdns.py'


@pytest.fixture
def mdns(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('mdns_records', MDNS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(module, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.setattr(module, '_get_local_ip', lambda: '192.0.2.10')
    monkeypatch.setattr(module, 'ServiceInfo', lambda **f: SimpleNamespace(**f))
    monkeypatch.setattr(module, 'IPVersion', SimpleNamespace(V4Only='v4'))
    return module


def build(mdns, env, name='ods', ip='192.0.2.10'):
    return mdns._build_services(env, name, ip)


class TestEnvParsing:
    def test_missing_env_file(self, mdns):
        assert mdns._read_env() == {}

    def test_comments_blanks_and_quotes(self, mdns):
        mdns.ENV_FILE.write_text(
            '# comment\n\nFOO = "quoted" \nBAR=\'single\'\nno-equals\n'
            'SPACED = a b \n', encoding='utf-8')
        env = mdns._read_env()
        assert env == {'FOO': 'quoted', 'BAR': 'single', 'SPACED': 'a b'}

    @pytest.mark.parametrize('raw,expected', [
        ('', 3001), ('abc', 3001), ('0', 3001), ('-5', 3001), ('65536', 3001),
        ('70000', 3001), ('8080', 8080), ('1', 1), ('65535', 65535)])
    def test_safe_port_bounds(self, mdns, raw, expected):
        assert mdns._safe_port({'P': raw}, 'P', 3001) == expected


class TestBindGating:
    @pytest.mark.parametrize('bind', ['', '127.0.0.1', 'localhost', '::1'])
    def test_loopback_skips_direct_srv(self, mdns, bind):
        records = build(mdns, {'BIND_ADDRESS': bind})
        kinds = {r.properties['kind'] for r in records}
        assert kinds == {'proxy'}
        assert all('direct' not in r.name for r in records)

    @pytest.mark.parametrize('bind', ['0.0.0.0', '192.168.1.5', '::'])
    def test_lan_bind_includes_direct_srv(self, mdns, bind):
        records = build(mdns, {'BIND_ADDRESS': bind})
        direct = [r for r in records if r.properties['kind'] == 'direct']
        assert {r.name.split('.')[0] for r in direct} == {
            'ods-dashboard', 'ods-chat', 'ods-dashboard-api', 'ods-hermes'}
        ports = {r.name[len('ods-'):].split('._http')[0]: r.port
                 for r in direct}
        assert ports == {'dashboard': 3001, 'chat': 3000,
                         'dashboard-api': 3002, 'hermes': 9119}

    def test_direct_records_point_at_device_host(self, mdns):
        records = build(mdns, {'BIND_ADDRESS': '0.0.0.0'}, name='office')
        for r in records:
            if r.properties['kind'] == 'direct':
                assert r.server == 'office.local.'
                assert r.properties['device'] == 'office'

    def test_custom_ports_propagate(self, mdns):
        records = build(mdns, {'BIND_ADDRESS': '0.0.0.0', 'WEBUI_PORT': '4000',
                               'ODS_PROXY_PORT': '8080'})
        chat = next(r for r in records if r.name.startswith('ods-chat.'))
        assert chat.port == 4000
        assert all(r.port == 8080 for r in records
                   if r.properties['kind'] == 'proxy')

    def test_proxy_records_always_published(self, mdns):
        records = build(mdns, {})
        servers = {r.server for r in records}
        assert 'ods.local.' in servers
        assert 'talk.ods.local.' in servers
        assert all(r.type_ == '_http._tcp.local.' for r in records)


class TestHostnameGate:
    @pytest.mark.parametrize('name', ['ods', 'a', 'my-box', 'A1', 'x' * 32])
    def test_valid_hostnames(self, mdns, name):
        assert mdns._HOSTNAME_RE.match(name)

    @pytest.mark.parametrize('name', ['', '-lead', 'trail-', 'has space',
                                      'under_score', 'x' * 33, 'a..b', '.dot'])
    def test_invalid_hostnames(self, mdns, name):
        assert not mdns._HOSTNAME_RE.match(name)

    def test_refresh_falls_back_to_ods(self, mdns, monkeypatch):
        registered = []
        monkeypatch.setattr(mdns, 'Zeroconf', lambda **kw: SimpleNamespace(
            register_service=registered.append))
        mdns.ENV_FILE.write_text('ODS_DEVICE_NAME=bad name!\n')
        announcer = mdns.Announcer()
        announcer.refresh()
        assert registered and all('bad' not in r.name for r in registered)
        assert any(r.server == 'ods.local.' for r in registered)


class TestConfigSignature:
    def test_signature_changes_on_any_input(self, mdns):
        announcer = mdns.Announcer()
        base = announcer._config_signature('ods', '192.0.2.10', {})
        assert announcer._config_signature('office', '192.0.2.10', {}) != base
        assert announcer._config_signature('ods', '10.0.0.1', {}) != base
        assert announcer._config_signature(
            'ods', '192.0.2.10', {'WEBUI_PORT': '4000'}) != base
        assert announcer._config_signature(
            'ods', '192.0.2.10', {'BIND_ADDRESS': '0.0.0.0'}) != base
        assert announcer._config_signature(
            'ods', '192.0.2.10', {'ODS_PROXY_PORT': '8080'}) != base
        # Unrelated keys never re-announce.
        assert announcer._config_signature(
            'ods', '192.0.2.10', {'UNRELATED': 'x'}) == base


class TestMainPlatformGate:
    @pytest.mark.parametrize('system', ['Darwin', 'Windows'])
    def test_non_linux_noop_zero(self, mdns, monkeypatch, system):
        monkeypatch.setattr('platform.system', lambda: system)
        assert mdns.main() == 0

    def test_linux_missing_env_returns_one(self, mdns, monkeypatch):
        monkeypatch.setattr('platform.system', lambda: 'Linux')
        monkeypatch.setattr(mdns, '_import_zeroconf_or_die', lambda: None)
        assert mdns.main() == 1
