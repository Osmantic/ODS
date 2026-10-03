import copy
import re

def normalize_sharing_response(raw, *, issued=False):
    """
    Validates and normalizes an ODS dashboard API response.

    Args:
        raw: The raw response dictionary.
        issued: Boolean flag indicating if credential/model keys are expected.

    Returns:
        A deep copy of the validated response.

    Raises:
        ValueError: If the response is invalid.
    """
    try:
        # 1. Validate root keys
        expected_root_keys = {'configuration', 'activeRoute', 'transport', 'runtime'}
        if issued:
            expected_root_keys.update({'credential', 'model'})

        if not isinstance(raw, dict):
            raise ValueError('invalid-sharing-response')

        if set(raw.keys()) != expected_root_keys:
            raise ValueError('invalid-sharing-response')

        # 2. Validate configuration
        config = raw['configuration']
        if not isinstance(config, dict):
            raise ValueError('invalid-sharing-response')

        expected_config_keys = {'schemaVersion', 'revision', 'enabled', 'devices'}
        if set(config.keys()) != expected_config_keys:
            raise ValueError('invalid-sharing-response')

        # schemaVersion strict int 1
        if not isinstance(config['schemaVersion'], int) or isinstance(config['schemaVersion'], bool):
            raise ValueError('invalid-sharing-response')
        if config['schemaVersion'] != 1:
            raise ValueError('invalid-sharing-response')

        # revision strict int 0..2**53-1
        if not isinstance(config['revision'], int) or isinstance(config['revision'], bool):
            raise ValueError('invalid-sharing-response')
        if not (0 <= config['revision'] <= 2**53 - 1):
            raise ValueError('invalid-sharing-response')

        # enabled strict bool
        if not isinstance(config['enabled'], bool):
            raise ValueError('invalid-sharing-response')

        # devices list length <=64
        devices = config['devices']
        if not isinstance(devices, list) or len(devices) > 64:
            raise ValueError('invalid-sharing-response')

        device_ids = set()
        for device in devices:
            if not isinstance(device, dict):
                raise ValueError('invalid-sharing-response')

            expected_device_keys = {
                'id', 'label', 'catalogId', 'runtimeModelId', 'createdAt',
                'expiresAt', 'revoked', 'maxConcurrent', 'maxOutputTokens',
                'deadlineSeconds', 'requestsPerMinute'
            }
            if set(device.keys()) != expected_device_keys:
                raise ValueError('invalid-sharing-response')

            # id matches device-[a-f0-9]{16}, unique
            if not isinstance(device['id'], str):
                raise ValueError('invalid-sharing-response')
            if not re.fullmatch(r'device-[a-f0-9]{16}', device['id']):
                raise ValueError('invalid-sharing-response')
            if device['id'] in device_ids:
                raise ValueError('invalid-sharing-response')
            device_ids.add(device['id'])

            # label, catalogId, runtimeModelId: nonempty trimmed printable ASCII <=256
            for key in ['label', 'catalogId', 'runtimeModelId']:
                val = device[key]
                if not isinstance(val, str):
                    raise ValueError('invalid-sharing-response')
                if not val or len(val) > 256:
                    raise ValueError('invalid-sharing-response')
                if not all(32 <= ord(c) <= 126 for c in val):
                    raise ValueError('invalid-sharing-response')
                if val != val.strip():
                    raise ValueError('invalid-sharing-response')

            # createdAt strict int 0..2**53-1
            if not isinstance(device['createdAt'], int) or isinstance(device['createdAt'], bool):
                raise ValueError('invalid-sharing-response')
            if not (0 <= device['createdAt'] <= 2**53 - 1):
                raise ValueError('invalid-sharing-response')

            # expiresAt strict int >createdAt and <=2**53-1
            if not isinstance(device['expiresAt'], int) or isinstance(device['expiresAt'], bool):
                raise ValueError('invalid-sharing-response')
            if not (device['createdAt'] < device['expiresAt'] <= 2**53 - 1):
                raise ValueError('invalid-sharing-response')

            # revoked strict bool
            if not isinstance(device['revoked'], bool):
                raise ValueError('invalid-sharing-response')

            # maxConcurrent int1..8
            if not isinstance(device['maxConcurrent'], int) or isinstance(device['maxConcurrent'], bool):
                raise ValueError('invalid-sharing-response')
            if not (1 <= device['maxConcurrent'] <= 8):
                raise ValueError('invalid-sharing-response')

            # maxOutputTokens int1..131072
            if not isinstance(device['maxOutputTokens'], int) or isinstance(device['maxOutputTokens'], bool):
                raise ValueError('invalid-sharing-response')
            if not (1 <= device['maxOutputTokens'] <= 131072):
                raise ValueError('invalid-sharing-response')

            # deadlineSeconds int1..3600
            if not isinstance(device['deadlineSeconds'], int) or isinstance(device['deadlineSeconds'], bool):
                raise ValueError('invalid-sharing-response')
            if not (1 <= device['deadlineSeconds'] <= 3600):
                raise ValueError('invalid-sharing-response')

            # requestsPerMinute int1..600
            if not isinstance(device['requestsPerMinute'], int) or isinstance(device['requestsPerMinute'], bool):
                raise ValueError('invalid-sharing-response')
            if not (1 <= device['requestsPerMinute'] <= 600):
                raise ValueError('invalid-sharing-response')

            # tokenHash must never appear (already checked by exact keys)

        # 3. Validate activeRoute
        active_route = raw['activeRoute']
        if active_route is None:
            pass
        elif isinstance(active_route, dict):
            expected_route_keys = {'catalogId', 'runtimeModelId', 'routeSeq', 'contextLength', 'capabilities'}
            if set(active_route.keys()) != expected_route_keys:
                raise ValueError('invalid-sharing-response')

            for key in ['catalogId', 'runtimeModelId']:
                val = active_route[key]
                if not isinstance(val, str):
                    raise ValueError('invalid-sharing-response')
                if not val or len(val) > 256:
                    raise ValueError('invalid-sharing-response')
                if not all(32 <= ord(c) <= 126 for c in val):
                    raise ValueError('invalid-sharing-response')
                if val != val.strip():
                    raise ValueError('invalid-sharing-response')

            # routeSeq strict int0..2**53-1
            if not isinstance(active_route['routeSeq'], int) or isinstance(active_route['routeSeq'], bool):
                raise ValueError('invalid-sharing-response')
            if not (0 <= active_route['routeSeq'] <= 2**53 - 1):
                raise ValueError('invalid-sharing-response')
            if type(active_route['contextLength']) is not int or not 1 <= active_route['contextLength'] <= 10_000_000:
                raise ValueError('invalid-sharing-response')
            capabilities = active_route['capabilities']
            if (not isinstance(capabilities, dict) or set(capabilities) != {'chat','tools','vision','agentViable'}
                    or any(type(value) is not bool for value in capabilities.values())):
                raise ValueError('invalid-sharing-response')
        else:
            raise ValueError('invalid-sharing-response')

        # 4. Validate transport
        transport = raw['transport']
        if not isinstance(transport, dict):
            raise ValueError('invalid-sharing-response')
        expected_transport_keys = {'mode', 'defaultPort', 'port'}
        if set(transport.keys()) != expected_transport_keys:
            raise ValueError('invalid-sharing-response')
        if transport['mode'] != 'loopback-only':
            raise ValueError('invalid-sharing-response')
        if not isinstance(transport['defaultPort'], int) or isinstance(transport['defaultPort'], bool):
            raise ValueError('invalid-sharing-response')
        if transport['defaultPort'] != 4005:
            raise ValueError('invalid-sharing-response')
        if type(transport['port']) is not int or not 1024 <= transport['port'] <= 65535:
            raise ValueError('invalid-sharing-response')

        # 5. Validate runtime
        runtime = raw['runtime']
        if not isinstance(runtime, dict):
            raise ValueError('invalid-sharing-response')
        expected_runtime_keys = {'status'}
        if set(runtime.keys()) != expected_runtime_keys:
            raise ValueError('invalid-sharing-response')
        if runtime['status'] not in ('not-probed','starting','ready','stopped','error','unavailable'):
            raise ValueError('invalid-sharing-response')

        # 6. Validate issued fields
        if issued:
            # model exactly 'ods/shared'
            if raw['model'] != 'ods/shared':
                raise ValueError('invalid-sharing-response')

            # credential exactly {id, key}
            credential = raw['credential']
            if not isinstance(credential, dict):
                raise ValueError('invalid-sharing-response')
            expected_credential_keys = {'id', 'key'}
            if set(credential.keys()) != expected_credential_keys:
                raise ValueError('invalid-sharing-response')

            # id must identify one non-revoked device in configuration
            cred_id = credential['id']
            if not isinstance(cred_id, str):
                raise ValueError('invalid-sharing-response')

            found_valid_device = False
            for device in devices:
                if device['id'] == cred_id and not device['revoked']:
                    found_valid_device = True
                    break
            if not found_valid_device:
                raise ValueError('invalid-sharing-response')

            # key must match ods_infer_[a-f0-9]{64}
            cred_key = credential['key']
            if not isinstance(cred_key, str):
                raise ValueError('invalid-sharing-response')
            if not re.fullmatch(r'ods_infer_[a-f0-9]{64}', cred_key):
                raise ValueError('invalid-sharing-response')
        else:
            # issued=False must reject any credential/model root keys (already handled by root key check)
            pass

        return copy.deepcopy(raw)

    except ValueError:
        raise
    except Exception:
        raise ValueError('invalid-sharing-response')
