"""Frozen per-turn provider gateway, shared by client and native lease workers.

No production service/config mutation. An immutable Settings revision and its
credential snapshot last only for this turn; later saves affect later turns.
"""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import uuid

from .client import _json,_write_private,read_private
from .config import public_config
from .store import StoreError,decode_document
from .vault import CredentialStore


class ProviderSession:
    def __init__(self,directory,*,expected_revision,confirmed,allow_cloud=False,handoff_provider_id=None,handoff_selection_scope=None):
        if handoff_selection_scope is not None and (handoff_selection_scope not in ('task','conversation','default') or handoff_provider_id is None):
            raise StoreError('invalid-handoff-selection-scope')
        if os.name != 'posix':
            raise StoreError('unsupported-platform')
        if confirmed is not True or type(expected_revision) is not int:
            raise StoreError('provider-confirmation-required')
        store = CredentialStore(directory)
        config = store.load()
        if config['revision'] != expected_revision:
            raise StoreError('stale-revision')
        if not config['enabled']:
            raise StoreError('provider-routing-disabled')
        by_id = {p['id']:p for p in config['providers']}
        self.handoff = None
        if handoff_provider_id is not None:
            # Internal owner-selected routing only. The conversational adapter
            # must additionally approve the actual checkpoint before inference.
            if (type(handoff_provider_id) is not str or not handoff_provider_id
                    or handoff_provider_id != config['roles']['handoff']):
                raise StoreError('handoff-recipient-not-configured')
            target = by_id[handoff_provider_id]
            previous = by_id[config['roles']['leader']]
            if (not target['enabled'] or target['id'] == previous['id']
                    or target['contextTokens'] < previous['contextTokens']
                    or target['maxOutputTokens'] < previous['maxOutputTokens']
                    or any(previous[key] and not target[key]
                           for key in ('supportsTools','supportsVision','reasoning'))):
                raise StoreError('handoff-recipient-incompatible')
            if target['kind'] == 'cloud' and not config['policy']['allowCloud']:
                raise StoreError('cloud-not-authorized')
            self.handoff = {key:target[key] for key in ('id','label','kind','baseUrl','model')}
            self.handoff.update(revision=expected_revision,scope='run',previousProviderId=previous['id'])
            if handoff_selection_scope is not None:
                self.handoff['selectionScope'] = handoff_selection_scope
            # Do not broaden the checkpoint's recipients to normal backups.
            config = copy.deepcopy(config)
            config['roles'].update(leader=handoff_provider_id,backups=[])
        selected = [config['roles']['leader'],*config['roles']['backups']]
        if any(by_id[pid]['kind'] == 'cloud' for pid in selected) and allow_cloud is not True:
            raise StoreError('cloud-transfer-confirmation-required')
        if any(by_id[pid]['model'] in ('ods/pixel','pixel/default','openclaw/default') for pid in selected):
            raise StoreError('provider-route-cycle')
        credentials = {pid:store.resolve_credential(pid,expected_revision=expected_revision) for pid in selected}
        if store.load()['revision'] != expected_revision:
            raise StoreError('stale-revision')
        self.config,self.credentials = config,credentials
        self.leader = by_id[config['roles']['leader']]
        self.events = []
        self.status = 'not-started'

    @contextmanager
    def serve(self):
        # Optional dependency import: default ODS/Pixel operation does not need
        # a provider gateway, and native host-agent import remains lightweight.
        try:
            import uvicorn
            from .runtime_gateway import create_app
        except ImportError:
            raise StoreError('provider-runtime-dependencies-missing') from None
        token = 'ods_route_'+secrets.token_hex(32)
        listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        server = thread = None
        try:
            listener.bind(('127.0.0.1',0))
            address = f'http://127.0.0.1:{listener.getsockname()[1]}/v1'
            app = create_app(self.config,self.credentials,token,events=self.events)
            server = uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False,
                timeout_graceful_shutdown=3,lifespan='off'))
            thread = threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
            thread.start()
            deadline = time.monotonic()+5
            while not server.started and thread.is_alive() and time.monotonic()<deadline:
                time.sleep(.01)
            if not server.started:
                raise StoreError('provider-runtime-start-failed')
            self.status = 'active-for-turn'
            lease = dict(baseUrl=address,token=token,contextTokens=self.leader['contextTokens'],
                maxOutputTokens=self.leader['maxOutputTokens'],reasoning=self.leader['reasoning'],
                supportsVision=self.leader['supportsVision'])
            if self.handoff is not None:
                lease['handoff'] = copy.deepcopy(self.handoff)
            yield lease
        finally:
            if server is not None:
                server.should_exit = True
            if thread is not None and thread.ident is not None:
                thread.join(timeout=6)
                if thread.is_alive():
                    server.force_exit = True
                    thread.join(timeout=2)
            listener.close()
            alive = thread is not None and thread.is_alive()
            self.status = 'cleanup-unverified' if alive else 'stopped'
            if alive:
                raise StoreError('provider-runtime-cleanup-unverified')

    @contextmanager
    def activate(self,run,env,agent_id):
        try:
            with self.serve() as lease:
                config = decode_document(read_private(Path(env['OPENCLAW_CONFIG_PATH'])))
                agents = config.get('agents',{}).get('list',[])
                if len(agents) != 1 or agents[0].get('id') != agent_id:
                    raise StoreError('provider-client-scope-mismatch')
                # Client-only overlay. Normal chat uses serve() without changing
                # the canonical agent, workspace, tools, sandbox or approvals.
                effective = copy.deepcopy(config)
                leader = self.leader
                model = dict(id='ods/pixel',name='ODS Provider Policy',reasoning=leader['reasoning'],
                    input=['text','image'] if leader['supportsVision'] else ['text'],
                    contextWindow=leader['contextTokens'],maxTokens=leader['maxOutputTokens'])
                provider_id = 'ods-runtime-'+uuid.uuid4().hex[:16]
                effective.setdefault('models',{})['providers'] = {provider_id:{
                    'baseUrl':lease['baseUrl'],'apiKey':lease['token'],'api':'openai-completions','models':[model]}}
                selected = {'primary':provider_id+'/ods/pixel','fallbacks':[]}
                effective['agents']['defaults']['model'] = selected
                effective['agents']['list'][0]['model'] = selected
                effective['agents']['defaults']['models'] = {provider_id+'/ods/pixel':{}}
                path = Path(run)/'provider-openclaw.json'
                _write_private(path,_json(effective))
                _write_private(Path(run)/'provider-policy.json',_json(public_config(self.config)))
                self.status = 'active-for-turn'
                yield dict(env,OPENCLAW_CONFIG_PATH=str(path),PIXEL_MODEL_REASONING=str(leader['reasoning']).lower())
        finally:
            _write_private(Path(run)/'provider-events.json',_json({'revision':self.config['revision'],
                'runtimeStatus':self.status,'events':self.events}))
