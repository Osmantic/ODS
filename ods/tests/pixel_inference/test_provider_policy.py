from copy import deepcopy
import pytest

from test_provider_runtime import configuration,payload
from pixel_provider.runtime_policy import select_candidates
from pixel_provider.store import StoreError


def test_order_only_automatic_roles_and_no_mutation():
    config,body = configuration(),payload()
    advisor = dict(config['providers'][0],id='advisor')
    config['providers'].append(advisor)
    config['roles'].update(advisor='advisor',handoff='advisor')
    before = deepcopy((config,body))
    selected,skipped = select_candidates(config,body)
    assert [p['id'] for p in selected]==['primary','backup'] and skipped==[]
    assert (config,body)==before
    selected[0]['model']='changed'
    assert (config,body)==before


@pytest.mark.parametrize('field,value',[('contextTokens',16384),('maxOutputTokens',128),
    ('supportsTools',False),('enabled',False)])
def test_incompatible_backup_is_explicitly_skipped(field,value):
    config = configuration(); config['providers'][1][field]=value
    selected,skipped = select_candidates(config,payload())
    assert [p['id'] for p in selected]==['primary']
    assert skipped==[{'providerId':'backup','reason':'incompatible-backup'}]


def test_max_attempts_and_cloud_checks():
    config = configuration(); config['policy']['maxAttempts']=1
    assert len(select_candidates(config,payload())[0])==1
    config['providers'][1]['kind']='cloud'
    with pytest.raises(StoreError,match='cloud-not-authorized'):
        select_candidates(config,payload())


@pytest.mark.parametrize('model',['ods/pixel','pixel/default','openclaw/default'])
def test_route_cycles_rejected(model):
    config = configuration(); config['providers'][1]['model']=model
    with pytest.raises(StoreError,match='provider-route-cycle'):
        select_candidates(config,payload())


def test_reasoning_requirement_preserved():
    config = configuration(); config['providers'][0]['reasoning']=True
    selected,skipped = select_candidates(config,payload())
    assert [p['id'] for p in selected]==['primary'] and len(skipped)==1


def test_unsupported_leader_and_disabled_policy():
    config = configuration(); config['providers'][0]['supportsTools']=False
    with pytest.raises(StoreError,match='leader-incompatible'):
        select_candidates(config,payload())
    config['enabled']=False
    with pytest.raises(StoreError,match='provider-routing-disabled'):
        select_candidates(config,payload())
