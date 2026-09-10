import json
import os
from pathlib import Path
import socket

import pytest

from test_provider_runtime import configuration
from pixel_provider.config import public_config
from pixel_provider.runtime_session import ProviderSession
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore
from pixel_provider.client import _json,_write_private,read_private

pytestmark = pytest.mark.skipif(os.name != 'posix',reason='POSIX adapter')


@pytest.fixture
def saved(tmp_path):
    root = tmp_path/'providers'; root.mkdir(mode=0o700)
    config = configuration(); config['revision']=0
    result = CredentialStore(root).save_public({'document':public_config(config),'expectedRevision':0,
        'credentialChanges':{'backup':{'action':'set','value':'test-private-backup-key'}}})
    return root,result


def test_confirmation_revision_and_credential_snapshot(saved):
    root,config = saved
    with pytest.raises(StoreError,match='provider-confirmation-required'):
        ProviderSession(root,expected_revision=1,confirmed=False)
    with pytest.raises(StoreError,match='stale-revision'):
        ProviderSession(root,expected_revision=0,confirmed=True)
    session = ProviderSession(root,expected_revision=1,confirmed=True)
    assert session.credentials['backup']=='test-private-backup-key'
    edited = dict(config); edited['roles']=dict(config['roles'],leader='backup',backups=['primary'])
    CredentialStore(root).save_public({'document':edited,'expectedRevision':1})
    assert session.config['roles']['leader']=='primary'
    assert session.config['revision']==1


def test_cloud_requires_separate_turn_authority(saved):
    root,config = saved
    config['providers'][1]['kind']='cloud'
    config['policy']['allowCloud']=True
    CredentialStore(root).save_public({'document':config,'expectedRevision':1,
        'credentialChanges':{'backup':{'action':'set','value':'test-private-cloud-key'}}})
    with pytest.raises(StoreError,match='cloud-transfer-confirmation-required'):
        ProviderSession(root,expected_revision=2,confirmed=True)
    assert ProviderSession(root,expected_revision=2,confirmed=True,allow_cloud=True)


def test_handoff_selects_only_configured_recipient_and_preserves_saved_leader(saved):
    root,config = saved
    config['roles']['handoff'] = 'backup'
    CredentialStore(root).save_public({'document':config,'expectedRevision':1,'credentialChanges':{}})
    with pytest.raises(StoreError,match='handoff-recipient-not-configured'):
        ProviderSession(root,expected_revision=2,confirmed=True,handoff_provider_id='primary')
    session = ProviderSession(root,expected_revision=2,confirmed=True,handoff_provider_id='backup')
    assert session.config['roles']['leader']=='backup'
    assert session.config['roles']['backups']==[]
    assert set(session.credentials)=={'backup'}
    assert session.handoff['previousProviderId']=='primary'
    with session.serve() as lease:
        assert lease['handoff']['id']=='backup'
        assert lease['handoff']['scope']=='run'
    saved_again = CredentialStore(root).load()
    assert saved_again['roles']['leader']=='primary'
    assert saved_again['roles']['backups']==['backup']
    assert ProviderSession(root,expected_revision=2,confirmed=True).leader['id']=='primary'


@pytest.mark.parametrize('field,value',[('contextTokens',16384),('maxOutputTokens',2048),('supportsTools',False)])
def test_handoff_rejects_capability_downgrades(saved,field,value):
    root,config = saved
    config['roles']['handoff']='backup'
    config['providers'][1][field]=value
    CredentialStore(root).save_public({'document':config,'expectedRevision':1,'credentialChanges':{}})
    with pytest.raises(StoreError,match='handoff-recipient-incompatible'):
        ProviderSession(root,expected_revision=2,confirmed=True,handoff_provider_id='backup')


def test_handoff_cloud_needs_per_run_transfer_consent(saved):
    root,config = saved
    config['roles']['handoff']='backup'
    config['providers'][1]['kind']='cloud'
    config['policy']['allowCloud']=True
    CredentialStore(root).save_public({'document':config,'expectedRevision':1,
        'credentialChanges':{'backup':{'action':'set','value':'cloud-fixture-only'}}})
    with pytest.raises(StoreError,match='cloud-transfer-confirmation-required'):
        ProviderSession(root,expected_revision=2,confirmed=True,handoff_provider_id='backup')
    session=ProviderSession(root,expected_revision=2,confirmed=True,handoff_provider_id='backup',allow_cloud=True)
    assert session.handoff['kind']=='cloud'


def test_run_overlay_preserves_tools_and_original_and_stops_listener(saved,tmp_path):
    root,_ = saved
    session = ProviderSession(root,expected_revision=1,confirmed=True)
    config = {'models':{'providers':{}},'agents':{'defaults':{'sandbox':{'mode':'all'}},'list':[{'id':'fixture'}]},
        'tools':{'exec':{'host':'sandbox'},'fs':{'workspaceOnly':True}}}
    source = tmp_path/'openclaw.json'; _write_private(source,_json(config))
    run = tmp_path/'run'; run.mkdir(mode=0o700)
    with session.activate(run,{'OPENCLAW_CONFIG_PATH':str(source)},'fixture') as env:
        effective = json.loads(read_private(Path(env['OPENCLAW_CONFIG_PATH'])))
        assert effective['tools']==config['tools']
        assert effective['agents']['defaults']['sandbox']=={'mode':'all'}
        assert effective['agents']['list'][0]['model']['fallbacks']==[]
        name,provider = next(iter(effective['models']['providers'].items()))
        assert name.startswith('ods-runtime-')
        assert provider['models'][0]['contextWindow']==32768
        port = int(provider['baseUrl'].split(':')[-1].split('/')[0])
        assert json.loads(read_private(source))==config
    assert session.status=='stopped'
    assert json.loads(read_private(run/'provider-events.json'))['runtimeStatus']=='stopped'
    with pytest.raises(OSError):
        socket.create_connection(('127.0.0.1',port),timeout=.2)
