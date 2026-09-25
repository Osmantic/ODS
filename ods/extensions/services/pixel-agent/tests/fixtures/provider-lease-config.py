"""Private synthetic provider configuration for the real gateway integration test."""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[5]/'bin'))
from pixel_provider.config import default_config,public_config
from pixel_provider.vault import CredentialStore

root,base_url = Path(sys.argv[1]),sys.argv[2]
root.mkdir(mode=0o700)
config = default_config()
config.update(enabled=True)
config['providers'] = [dict(id='fixture',label='Synthetic fixture',kind='local',baseUrl=base_url,
    model='fixture-model',contextTokens=32768,maxOutputTokens=4096,supportsTools=True,supportsVision=False,
    reasoning=False,credentialRef=None,enabled=True)]
config['roles'].update(leader='fixture',backups=[])
changes={'fixture':dict(action='set',value='fixture-upstream-key')}
if '--handoff' in sys.argv[3:]:
    config['providers'].append(dict(config['providers'][0],id='stronger',label='Stronger fixture',model='stronger-model'))
    config['roles']['handoff']='stronger'
    changes['stronger']=dict(action='set',value='stronger-upstream-key')
CredentialStore(root).save_public(dict(document=public_config(config),expectedRevision=0,
    credentialChanges=changes))
if os.environ.get('ODS_PREPARE_LEASE_RUNTIME')=='1':
    from pixel_provider.advice_runtime import prepare_runtime
    prepare_runtime(root,python='/usr/bin/python3.12',expected_revision=0,confirmed=True)
