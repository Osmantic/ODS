"""Credential-free, strict result of an owner-confirmed metadata probe."""
import copy
import re
import time
from urllib.parse import urlsplit


def normalize_connection_result(value):
    def exact(item, keys):
        return type(item) is dict and set(item) == set(keys)

    def integer(item, low, high):
        return type(item) is int and low <= item <= high

    def text(item):
        return (type(item) is str and 1 <= len(item) <= 256 and item == item.strip()
                and all(32 <= ord(char) < 127 for char in item))

    try:
        if (not exact(value, ('schemaVersion', 'endpoint', 'deviceId', 'expiresAt', 'expected', 'metadata'))
                or not integer(value['schemaVersion'], 1, 1)
                or not integer(value['expiresAt'], 0, 2**53 - 1)
                or value['expiresAt'] <= time.time()
                or type(value['deviceId']) is not str
                or not re.fullmatch(r'device-[a-f0-9]{16}', value['deviceId'])
                or not exact(value['expected'], ('catalogId', 'runtimeModelId'))
                or not all(text(item) for item in value['expected'].values())):
            raise ValueError('invalid-connection-result')
        endpoint = value['endpoint']
        if (type(endpoint) is not str or not 1 <= len(endpoint) <= 2048
                or any(char.isspace() or ord(char) < 32 or char in '\\@?#' for char in endpoint)):
            raise ValueError('invalid-connection-result')
        url = urlsplit(endpoint)
        if (url.scheme not in {'http', 'https'} or not url.hostname or url.path != '/v1'
                or url.port is not None and not 1 <= url.port <= 65535):
            raise ValueError('invalid-connection-result')
        metadata = value['metadata']
        if (not exact(metadata, ('catalogId', 'routedModel', 'identitySource', 'routeSeq',
                                'contextLength', 'capabilities', 'maxOutputTokens', 'expiresAt', 'execution'))
                or metadata['catalogId'] != value['expected']['catalogId']
                or metadata['routedModel'] != value['expected']['runtimeModelId']
                or metadata['identitySource'] != 'ods-verified-route'
                or metadata['execution'] != 'client-owned'
                or not integer(metadata['routeSeq'], 0, 2**53 - 1)
                or not integer(metadata['contextLength'], 4096, 10_000_000)
                or not integer(metadata['maxOutputTokens'], 256, min(131072, metadata['contextLength']))
                or type(metadata['expiresAt']) is not int or metadata['expiresAt'] != value['expiresAt']
                or not exact(metadata['capabilities'], ('chat', 'tools', 'vision', 'agentViable'))
                or any(type(item) is not bool for item in metadata['capabilities'].values())
                or metadata['capabilities']['chat'] is not True):
            raise ValueError('invalid-connection-result')
        return copy.deepcopy(value)
    except (KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError('invalid-connection-result') from None
