"""Serialized reviewed-app journeys across real API and host HTTP services."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'linux', reason='The Root/owner fixture uses Linux boundaries')
@pytest.mark.parametrize('scenario', ['normal', 'lost-exchange'])
def test_reviewed_app_consent_activation_recovery_and_withdrawal_over_http(tmp_path, scenario):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'TMPDIR'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(HOME=str(tmp_path), ODS_QA_REPORT_DIR=str(tmp_path), ODS_QA_NATIVE_SCENARIO=scenario,
               PYTHONDONTWRITEBYTECODE='1')
    child = Path(__file__).parent / 'fixtures/native_apps_http.py'
    result = subprocess.run([sys.executable, str(child)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-5000:]
    report = json.loads((tmp_path / 'native-http-journey-results.json').read_text())
    for key in ('fullDashboardApi', 'realAuthenticatedHostHttp', 'realAuthenticatedNodeHttp',
                'realFramedUnixSocket', 'invalidInputRedacted', 'actualRootOwnerTransactions',
                'wrongStatePreservedFlow', 'singleExchange', 'readyOnlyAfterScopedProof',
                'statusDidNoProviderWork', 'explicitCheckWasReadonly', 'withdrawalVerifiedBeforeLocalForget'):
        assert report[key] is True
    assert report['loopbackCallback'] == (scenario == 'normal')
    assert report['lostExchangeStayedUnconfirmed'] == (scenario == 'lost-exchange')
    assert report['providerRevoked'] is False and not report['installedRuntimeTouched'] and not report['realAccountContacted']
    assert report['simulatedBoundaries'] == ['native OAuth/provider', 'systemd/gateway processes', 'privileged transport and UID custody']
