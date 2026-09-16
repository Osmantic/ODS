"""Run with python -I -S; no pytest or installed third-party packages required."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.advice_jobs import AdvisoryJobs
from pixel_provider.advice_runtime import runtime_status
from pixel_provider.advice_setup import readiness
from pixel_provider.handoff_approvals import HandoffApprovals
from pixel_provider.handoff_worker import read_request
from pixel_provider.scopes import ScopeStore

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    assert runtime_status(root)==dict(status='missing',revision=0,runtimeId=None)
    assert AdvisoryJobs(root).providers==root
    assert not (root/'provider-config.json').exists()
    assert readiness(root/'absent')['status']=='not-configured'
    assert HandoffApprovals(root/'absent').pending()==dict(items=[],unavailableCount=0)
    assert callable(read_request)
    assert ScopeStore(root/'absent').status('stdlib-chat')['runtimeStatus']=='preference-only'
    assert 'httpx' not in sys.modules and 'fastapi' not in sys.modules
print('Host advisory imports/status pass with stdlib only:',sys.version.split()[0])
