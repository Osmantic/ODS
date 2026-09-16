"""Versioned, inference-only client import. No tools or runtime activation."""
import copy
import re
import time
from urllib.parse import urlsplit

from .config import _validate_base_url
from .store import StoreError


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _text(value):
    return isinstance(value, str) and value == value.strip() and 1 <= len(value) <= 256 and all(32 <= ord(c) < 127 for c in value)


def connection_url(value):
    try:
        result = _validate_base_url(value, 'connection')
        parts = urlsplit(result)
        if parts.path != '/v1':
            raise ValueError('path')
        # HTTP localhost is always literal loopback in the saved client config,
        # not a hostname whose later resolution could send the key elsewhere.
        if parts.scheme == 'http' and parts.hostname == 'localhost':
            result = 'http://127.0.0.1' + (f':{parts.port}' if parts.port else '') + '/v1'
        return result
    except (ValueError, TypeError, AttributeError):
        raise StoreError('invalid-connection') from None


def normalize_connection(value, *, now=None):
    stamp = time.time() if now is None else now
    try:
        if (not isinstance(value, dict) or set(value) != {'schemaVersion','kind','label','baseUrl','model',
                'deviceId','expiresAt','expected','credential','execution'}
                or not _integer(value['schemaVersion'],1,1) or value['kind'] != 'ods-inference-connection'
                or not _text(value['label']) or value['model'] != 'ods/shared' or value['execution'] != 'client-owned'
                or not isinstance(value['deviceId'],str) or not re.fullmatch(r'device-[a-f0-9]{16}',value['deviceId'])
                or not _integer(value['expiresAt'],0,2**53-1) or not stamp < value['expiresAt']
                or not isinstance(value['expected'],dict) or set(value['expected']) != {'catalogId','runtimeModelId'}
                or any(not _text(item) for item in value['expected'].values())
                or not isinstance(value['credential'],dict) or set(value['credential']) != {'apiKey'}
                or not isinstance(value['credential']['apiKey'],str)
                or not re.fullmatch(r'ods_infer_[a-f0-9]{64}',value['credential']['apiKey'])):
            raise ValueError('schema')
        result = copy.deepcopy(value)
        result['baseUrl'] = connection_url(value['baseUrl'])
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StoreError('invalid-connection') from None


def normalize_probe(value, connection, *, now=None):
    try:
        connection = normalize_connection(connection, now=now)
        metadata = value['ods']
        expected = connection['expected']
        caps = metadata['capabilities']
        if (value.get('object') != 'list' or not isinstance(value['data'],list) or len(value['data']) != 1
                or value['data'][0]['id'] != 'ods/shared'
                or not isinstance(metadata,dict) or set(metadata) != {'catalogId','routedModel','identitySource',
                    'routeSeq','contextLength','capabilities','maxOutputTokens','expiresAt','execution'}
                or metadata['catalogId'] != expected['catalogId'] or metadata['routedModel'] != expected['runtimeModelId']
                or metadata['identitySource'] != 'ods-verified-route' or metadata['execution'] != 'client-owned'
                or not _integer(metadata['routeSeq'],0,2**53-1)
                or not _integer(metadata['contextLength'],4096,10_000_000)
                or not _integer(metadata['maxOutputTokens'],256,min(131072,metadata['contextLength']))
                or not _integer(metadata['expiresAt'],0,2**53-1) or metadata['expiresAt'] != connection['expiresAt']
                or not isinstance(caps,dict) or set(caps) != {'chat','tools','vision','agentViable'}
                or any(type(item) is not bool for item in caps.values()) or caps['chat'] is not True):
            raise ValueError('schema')
        return copy.deepcopy(metadata)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        raise StoreError('invalid-probe') from None
