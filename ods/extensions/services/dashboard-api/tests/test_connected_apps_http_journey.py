"""Serialized owner connection flow across actual API and host HTTP services.

Google, gateway/systemd processes and privileged UID custody are simulated.
This does not replace installed Root/real account/platform acceptance.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'linux', reason='The connection coordinator uses Linux/systemd boundaries')
@pytest.mark.parametrize('mode,scenario', [('legacy', 'normal'), ('managed', 'normal'), ('managed', 'failed-callback')])
def test_google_connection_survives_wrong_callback_and_disconnects_through_http(tmp_path, mode, scenario):
    # Fresh process avoids changing the parent suite's cached API configuration,
    # callback listener, monkeypatches, HTTP clients or framework lifespan.
    allowed = {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'TMPDIR'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(HOME=str(tmp_path), ODS_QA_REPORT_DIR=str(tmp_path), ODS_QA_GOOGLE_MODE=mode,
               ODS_QA_GOOGLE_SCENARIO=scenario, PYTHONDONTWRITEBYTECODE='1')
    child = Path(__file__).parent / 'fixtures/connected_apps_http.py'
    result = subprocess.run([sys.executable, str(child)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (result.stdout[-3000:] + result.stderr[-5000:])
    report = json.loads((tmp_path / 'connected-http-journey-results.json').read_text())
    assert report['fullDashboardApi'] and report['realAuthenticatedHostHttp']
    assert report['actualCoordinatorBrokerTransaction']
    assert report['forgedCallbackPreservesFlow'] and report['singleExchange']
    assert report['disconnectDeletesToken'] and not report['installedRuntimeTouched']
    assert report['readGrantsWithdrawn']
    assert report['managedDeploymentClient'] == (mode == 'managed')
    assert report['browserClientSupplied'] == (mode == 'legacy')
    assert report['failedCallbackRecovered'] == (scenario == 'failed-callback')
    assert report['simulatedBoundaries'] == [
        'Google', 'systemd/gateway processes', 'privileged transport and UID custody',
    ]
