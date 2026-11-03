"""Run the real health CLI against a disposable live integrations service."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

if sys.platform == 'win32':
    pytest.skip('Unix integrations service; run under Linux/WSL', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import integrations_service as service


@pytest.mark.skipif(sys.platform == 'darwin', reason='Native owner-key custody has its own transport tests')
@pytest.mark.parametrize('agent_available', [True, False])
def test_actual_health_cli_checks_both_protocol_endpoints(agent_available):
    with tempfile.TemporaryDirectory(prefix='ods-health-') as name:
        root = Path(name)
        agent, state = root / 'agent.sock', root / 'state'
        servers = service.start(str(agent), str(state))
        try:
            # Endpoint construction and replies are the real service, not mocks.
            result = subprocess.run([sys.executable, '-I', '-B', service.__file__, 'health',
                str(agent if agent_available else root / 'absent.sock'), str(state)],
                capture_output=True, text=True, timeout=15)
            if agent_available:
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout) == {'schemaVersion': 1, 'status': 'ready', 'owner': True, 'agent': True}
            else:
                assert result.returncode != 0
                assert '"status": "ready"' not in result.stdout
            # Health uses settings/catalog reads only: no approval or provider work.
            assert servers.service.store.approvals() == []
            assert servers.service.local.running == {}
        finally:
            servers.close()
