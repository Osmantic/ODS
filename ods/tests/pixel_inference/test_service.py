import copy
import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider import sharing_service as service_module
from pixel_provider.sharing_service import SharingService, validate_compose
from pixel_provider.store import StoreError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(service_module.os, 'getegid', lambda: 1000)
    extension = tmp_path / 'extension'
    extension.mkdir()
    template = extension / 'compose.yaml.disabled'
    template.write_text('services: {}')
    template.chmod(0o644)
    private = tmp_path / 'data/pixel-inference'
    spec = {'services': {'pixel-inference': {
        'container_name':'ods-pixel-inference','user':'1000:1000','read_only':True,
        'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
        'ports':[{'host_ip':'127.0.0.1','published':'4005','target':8093,'protocol':'tcp'}],
        'volumes':[{'type':'bind','source':str(private),'target':'/state','read_only':True,'bind':{'create_host_path':False}}],
        'environment':{'ODS_INFERENCE_STATE_DIR':'/state','ODS_INFERENCE_ROUTER_URL':'http://model-router:9099'},
    }}}
    calls = []
    state = {'running':False,'existed':False,'fail_build':False,'collision':False,
             'activation':None,'fail_up':False,'timeout_up':False,'health':'healthy','takeover':False}
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs['cwd'] == str(tmp_path)
        rc, out, err = 0, '', ''
        if args[:3] == ['docker','container','inspect']:
            router = args[-1] == 'ods-model-router'
            if not router and not state['existed']:
                return SimpleNamespace(returncode=1,stdout='',stderr='Error: No such container')
            labels = {'com.docker.compose.service':'model-router' if router else 'pixel-inference',
                      'com.docker.compose.project.working_dir':str(tmp_path)}
            if state['collision'] and not router:
                labels['com.docker.compose.project.working_dir'] = '/different-install'
            if not router and state['activation']:
                labels[service_module.ACTIVATION_LABEL] = state['activation']
            out = json.dumps([{'Id':'a'*64,'Config':{'Labels':labels},'State':{
                'Running':True if router else state['running'],'Health':{'Status':'healthy' if router else state['health']}}}])
        elif 'config' in args:
            out = json.dumps(spec)
        elif 'build' in args:
            rc = 1 if state['fail_build'] else 0
        elif 'up' in args:
            state.update(running=True,existed=True)
            override = Path([args[i+1] for i,arg in enumerate(args) if arg == '-f'][-1])
            state['activation'] = json.loads(override.read_text())['services']['pixel-inference']['labels'][service_module.ACTIVATION_LABEL]
            if state['takeover']:
                state['activation'] = 'a-different-operation'
            if state['timeout_up']:
                raise subprocess.TimeoutExpired(args, 60)
            rc = 1 if state['fail_up'] else 0
        elif args[:3] == ['docker','container','stop']:
            state['running'] = False
        else:
            pytest.fail('unexpected command '+str(args))
        return SimpleNamespace(returncode=rc,stdout=out,stderr=err)
    invalidations = []
    service = SharingService(tmp_path,tmp_path/'data',extension,port=4005,
        resolve_flags=lambda:['-f','base.yml','-f','extension/compose.yaml'],
        invalidate=lambda: invalidations.append(True), run=run)
    return service,spec,calls,state,extension,private


def test_start_only_builds_and_starts_sharing_then_stop_disables(setup):
    service,spec,calls,state,extension,_ = setup
    assert service.status() == {'status':'stopped'}
    service.start()
    assert service.status() == {'status':'ready'}
    assert (extension/'compose.yaml').is_file()
    build = next(command for command in calls if 'build' in command)
    up = next(command for command in calls if 'up' in command)
    assert build[-2:] == ['build','pixel-inference']
    assert up[-7:] == ['up','-d','--no-deps','--no-build','--pull','never','pixel-inference']
    service.stop()
    assert service.status() == {'status':'stopped'}
    assert (extension/'compose.yaml.disabled').is_file()
    assert ['docker','container','stop','--time','10','a'*64] in calls


def test_failed_build_restores_owned_template_without_up(setup):
    service,_,calls,state,extension,_ = setup
    state['fail_build'] = True
    with pytest.raises(StoreError,match='sharing-build-failed'):
        service.start()
    assert not any('up' in command for command in calls)
    assert (extension/'compose.yaml.disabled').is_file()
    assert not (extension/'compose.yaml').exists()


def test_conflicting_template_and_container_are_not_overwritten(setup):
    service,_,calls,state,extension,_ = setup
    (extension/'compose.yaml').write_text('owner edit')
    (extension/'compose.yaml').chmod(0o644)
    with pytest.raises(StoreError,match='sharing-compose-conflict'):
        service.start()
    assert (extension/'compose.yaml').read_text() == 'owner edit'
    state.update(existed=True,running=True,collision=True)
    with pytest.raises(StoreError,match='sharing-container-identity-mismatch'):
        service.stop()
    assert not any('stop' in command for command in calls)


@pytest.mark.parametrize('change', [
    lambda svc: svc.update(privileged=True),
    lambda svc: svc.update(user='0:0'),
    lambda svc: svc.update(read_only=False),
    lambda svc: svc.update(cap_add=['SYS_ADMIN']),
    lambda svc: svc.update(cap_drop=[]),
    lambda svc: svc.update(security_opt=[]),
    lambda svc: svc.update(network_mode='host'),
    lambda svc: svc['ports'][0].update(host_ip='0.0.0.0'),
    lambda svc: svc['ports'][0].update(published='4006'),
    lambda svc: svc['volumes'][0].update(source='/'),
    lambda svc: svc['volumes'][0].update(read_only=False),
    lambda svc: svc['volumes'][0]['bind'].update(create_host_path=True),
    lambda svc: svc['environment'].update(ODS_INFERENCE_ROUTER_URL='http://evil'),
])
def test_effective_compose_rejects_unsafe_changes(setup,change):
    service,spec,calls,_,_,private = setup
    change(spec['services']['pixel-inference'])
    with pytest.raises(StoreError,match='unsafe-sharing-compose'):
        service.start()
    assert not any('build' in command or 'up' in command for command in calls)


def test_private_read_only_compose_contract(setup):
    _,spec,_,_,_,private = setup
    validate_compose(copy.deepcopy(spec),directory=private,port=4005,uid=1000,gid=1000)


@pytest.mark.parametrize('failure', ['fail_up','timeout_up'])
def test_partial_up_cleans_only_nonce_bound_immutable_id(setup, failure):
    service,_,calls,state,extension,_ = setup
    state[failure] = True
    with pytest.raises(StoreError):
        service.start()
    assert not state['running']
    assert ['docker','container','stop','--time','10','a'*64] in calls
    assert (extension/'compose.yaml.disabled').is_file()
    assert not list(service.install_dir.glob('.ods-sharing-activation-*.json'))


def test_health_failure_stops_its_own_new_container(setup, monkeypatch):
    service,_,calls,state,extension,_ = setup
    state['health'] = 'unhealthy'
    ticks = iter([0,1,31])
    monkeypatch.setattr(service_module.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(service_module.time, 'sleep', lambda _: None)
    with pytest.raises(StoreError, match='sharing-health-unverified'):
        service.start()
    assert not state['running']
    assert ['docker','container','stop','--time','10','a'*64] in calls


def test_foreign_activation_is_neither_claimed_ready_nor_stopped(setup):
    service,_,calls,state,_,_ = setup
    state['takeover'] = True
    with pytest.raises(StoreError, match='sharing-activation-changed'):
        service.start()
    assert state['running']
    assert not any('stop' in command for command in calls)


def test_failed_build_does_not_stop_preexisting_running_container(setup):
    service,_,calls,state,_,_ = setup
    state.update(existed=True,running=True,activation='previous-owner-activation',fail_build=True)
    with pytest.raises(StoreError, match='sharing-build-failed'):
        service.start()
    assert state['running']
    assert not any('stop' in command for command in calls)
