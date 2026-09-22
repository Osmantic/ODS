#!/usr/bin/env python3
"""Regression: temperature must be numeric and within [0.0, 2.0]."""
import importlib.util, os, sys, tempfile, types

def _load_main():
    """Import the inference module with stubbed dependencies."""
    # Stub pixel_provider before importing main
    sharing = types.ModuleType('pixel_provider.sharing')
    sharing.PUBLIC_MODEL = 'test-model'
    sharing.SharingStore = type('SharingStore', (), {'__init__': lambda s, p: None})
    store = types.ModuleType('pixel_provider.store')
    store.MAX_BYTES = 4 * 1024 * 1024
    store.StoreError = type('StoreError', (Exception,), {'__init__': lambda s, c='': setattr(s, 'code', c)})
    store.decode_document = lambda b: __import__('json').loads(b)
    sys.modules['pixel_provider'] = types.ModuleType('pixel_provider')
    sys.modules['pixel_provider.sharing'] = sharing
    sys.modules['pixel_provider.store'] = store
    sys.modules['anyio'] = types.ModuleType('anyio')
    sys.modules['httpx'] = types.ModuleType('httpx')
    sys.modules['fastapi'] = types.ModuleType('fastapi')
    sys.modules['fastapi.responses'] = types.ModuleType('fastapi.responses')
    spec = importlib.util.spec_from_file_location(
        'main', os.path.join(os.path.dirname(__file__), '..', 'extensions', 'services', 'pixel-inference', 'app', 'main.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _base_payload(**overrides):
    p = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hi'}]}
    p.update(overrides)
    return p

def _grant():
    return {'maxOutputTokens': 4096, 'maxConcurrent': 2, 'requestsPerMinute': 10,
            'deadlineSeconds': 30, 'id': 'test', 'catalogId': 'c', 'runtimeModelId': 'r', 'expiresAt': ''}

def test_valid_temperatures():
    mod = _load_main()
    for t in [0, 0.0, 0.5, 1.0, 1.5, 2.0]:
        result = mod._prepare(_base_payload(temperature=t), _grant())
        assert result['temperature'] == t, f"Expected {t}, got {result.get('temperature')}"
    print("PASS: valid temperatures accepted")

def test_negative_temperature():
    mod = _load_main()
    try:
        mod._prepare(_base_payload(temperature=-0.1), _grant())
        assert False, "Should have raised ShareError"
    except mod.ShareError as e:
        assert e.status == 400 and e.code == 'invalid_temperature'
    print("PASS: negative temperature rejected")

def test_over_range_temperature():
    mod = _load_main()
    try:
        mod._prepare(_base_payload(temperature=2.1), _grant())
        assert False, "Should have raised ShareError"
    except mod.ShareError as e:
        assert e.status == 400 and e.code == 'invalid_temperature'
    print("PASS: temperature > 2.0 rejected")

def test_boolean_temperature():
    mod = _load_main()
    try:
        mod._prepare(_base_payload(temperature=True), _grant())
        assert False, "Should have raised ShareError"
    except mod.ShareError as e:
        assert e.status == 400 and e.code == 'invalid_temperature'
    print("PASS: boolean temperature rejected")

def test_string_temperature():
    mod = _load_main()
    try:
        mod._prepare(_base_payload(temperature="hot"), _grant())
        assert False, "Should have raised ShareError"
    except mod.ShareError as e:
        assert e.status == 400 and e.code == 'invalid_temperature'
    print("PASS: string temperature rejected")

if __name__ == '__main__':
    test_valid_temperatures()
    test_negative_temperature()
    test_over_range_temperature()
    test_boolean_temperature()
    test_string_temperature()
    print("\nAll temperature bounds tests passed.")
