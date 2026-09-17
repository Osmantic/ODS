from copy import deepcopy
import hashlib
import json
import pytest

from model_mtp import mtp_metadata, parse_runtime_capability, recommend_mtp, qualify_memory_fit


@pytest.mark.parametrize('declared,expected,compatible', [
    ('--load-mode MODE\n', ['--load-mode','mmap'], False),
    ('--mmap, --no-mmap\n', ['--mmap'], True),
    ('--load-mode MODE\n--mmap, --no-mmap\n', ['--load-mode','mmap'], True),
    ('Description mentions --mmap and --no-mmap but neither is a flag.\n', [], False),
])
def test_runtime_probe_distinguishes_modern_loading_from_router_compatibility(tmp_path, monkeypatch, declared, expected, compatible):
    import model_mtp
    import subprocess
    runtime = tmp_path/'runtime'; runtime.write_bytes(b'fixture')
    calls = []
    def run(command, **kwargs):
        calls.append((command,kwargs))
        return subprocess.CompletedProcess(command,0,stdout=declared+'--spec-type none,draft-mtp\n--spec-draft-n-max N\n')
    monkeypatch.setattr(model_mtp.subprocess, 'run', run)
    result = model_mtp.probe_runtime(runtime)
    assert result['mtp'] is True
    assert result['loadModeArguments'] == expected
    assert result['lemonade10MmapCompatible'] is compatible
    assert calls[0][0] == [str(runtime.resolve()), '--help']
    assert calls[0][1]['creationflags'] == getattr(subprocess,'CREATE_NO_WINDOW',0)


def test_complete_launch_arguments_are_parsed_without_loading_or_exposing_output(monkeypatch):
    import model_mtp
    import subprocess
    calls = []
    def run(command, **kwargs):
        calls.append((command,kwargs))
        if '--removed-option' in command:
            return subprocess.CompletedProcess(command,1,stdout='invalid argument with private-local-path')
        return subprocess.CompletedProcess(command,0,stdout='--ctx-size N\n--model FILE\n')
    monkeypatch.setattr(model_mtp.subprocess,'run',run)
    launch = ['llama-server','--model','private-local-path','--load-mode','mmap']
    model_mtp.validate_runtime_command(launch)
    assert calls[0][0] == [*launch,'--help']
    assert calls[0][1]['stdin'] == subprocess.DEVNULL
    assert calls[0][1].get('shell',False) is False
    assert calls[0][1]['creationflags'] == getattr(subprocess,'CREATE_NO_WINDOW',0)
    with pytest.raises(ValueError,match='rejected its launch arguments') as failure:
        model_mtp.validate_runtime_command([*launch,'--removed-option'])
    assert 'private-local-path' not in str(failure.value)


def test_ngram_only_runtime_is_not_mtp_capable():
    old = "--spec-type [none|ngram-cache|ngram-simple]\n type of speculative decoding\n--draft-max N\n"
    assert parse_runtime_capability(old)["mtp"] is False
    current = "--spec-type none,draft-simple,draft-mtp,ngram-mod\n type of speculative decoding\n--spec-draft-n-max N\n"
    assert parse_runtime_capability(current)["mtp"] is True
    assert parse_runtime_capability("Use draft-mtp models.\n--spec-type none,ngram-mod\n--spec-draft-n-max N")["mtp"] is False


def test_real_metadata_not_filename_decides_embedded_support():
    assert mtp_metadata({"name":"Amazing MTP model"}, {"readable":True,"metadata":{}}) is None
    declared = {"mtp":{"source_url":"https://unsloth.ai/docs/models/mtp"}}
    assert mtp_metadata(declared, {})["modelSupport"] == "publisher-declared"
    actual = mtp_metadata(declared, {"metadata":{"qwen35.nextn_predict_layers":1}})
    assert actual["modelSupport"] == "embedded"
    assert actual["defaultEnabled"] is False
    assert actual["recommendation"] == "benchmark-required"
    assert mtp_metadata({}, {"metadata":{"qwen35.nextn_predict_layers":True}}) is None


def evidence():
    return {"signature":"same-model-runtime-hardware-context",
            "baseline":[{"valid":True,"tokens":256,"milliseconds":10000} for _ in range(3)],
            "mtp":[{"valid":True,"tokens":256,"milliseconds":7500,"acceptedDraftTokens":100} for _ in range(3)]}


def test_mtp_is_recommended_only_for_matching_valid_paired_measurements():
    measured=evidence()
    assert recommend_mtp(measured,measured["signature"]) == {"recommendation":"mtp","speedup":1.333}
    assert recommend_mtp(measured,"different-hardware-or-context")["recommendation"] == "benchmark-required"
    for bad in (None, {}, {**measured,"mtp":measured["mtp"][:1]}):
        assert recommend_mtp(bad,measured["signature"])["recommendation"] == "benchmark-required"
    for key,value in (("valid",False),("tokens",10),("acceptedDraftTokens",0),("milliseconds",float("nan"))):
        invalid=deepcopy(measured);invalid["mtp"][0][key]=value
        assert recommend_mtp(invalid,measured["signature"])["recommendation"] == "benchmark-required"


def test_slower_or_negligible_mtp_gain_keeps_baseline():
    measured=evidence()
    for duration in (12000,9900):
        for sample in measured["mtp"]: sample["milliseconds"]=duration
        assert recommend_mtp(measured,measured["signature"])["recommendation"] == "baseline"


def test_catalog_mtp_contract_survives_model_payload(tmp_path):
    from performance_oracle import build_models_payload
    from models import ModelLibraryResponse
    raw={"id":"qwen3.8-27b-iq4-xs","name":"Qwen 3.8 27B","gguf_file":"qwen.gguf",
         "size_mb":14253,"vram_required_gb":18,"context_length":32768,"max_context_length":262144,
         "mtp":{"mode":"embedded","source_url":"https://unsloth.ai/docs/models/mtp"}}
    payload=build_models_payload(None,None,0,tmp_path,catalog=[raw],evidence=[])
    encoded=ModelLibraryResponse(**payload).model_dump()
    value=encoded["models"][0]["metadata"]["mtp"]
    assert value["modelSupport"] == "publisher-declared"
    assert value["runtimeCheckRequired"] is True
    assert value["defaultEnabled"] is False


def memory_evidence():
    profile = {'modelSha256':'a'*64,'runtimeSha256':'b'*64,'hardware':'AMD-Radeon-RX-9070-XT-Vulkan',
        'context':16384,'draftTokens':2,'cacheType':'q4_0','parallel':1,'gpuLayers':'99','flashAttention':'on',
        'launchMode':'lemonade','visionProjectorSha256':'c'*64}
    signature = hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()
    qualified = {'profile':profile,'signature':signature}
    execution = {'qualificationSignature':signature,'runtimeMode':'lemonade','backend':'vulkan','gpuLayers':'99',
        'context':16384,'draftTokens':2,'cacheType':'q4_0','visionProjectorSha256':'c'*64,'visionProjectorFile':'mmproj-F16.gguf'}
    execution['signature'] = hashlib.sha256(json.dumps(execution,sort_keys=True).encode()).hexdigest()
    hardware = {'name':'AMD Radeon RX 9070 XT','backend':'amd','memoryTotalMB':16188,'systemRamGB':61}
    measured = {**evidence(), 'signature':signature,'profile':profile,'status':'completed','execution':execution,
        'conditions':{'isolatedFromLemonade':True,'visionProjectorLoaded':True},'hardware':hardware,
        'memorySnapshots':[{'mode':mode,**hardware,'memoryUsedMB':15000} for mode in ('baseline','mtp')]}
    return qualified, measured


def test_only_actual_lemonade_memory_configuration_qualifies():
    qualified, measured = memory_evidence()
    fit = qualify_memory_fit(measured, qualified)
    assert fit['contextLength'] == 16384
    assert fit['observedGpuMemoryMB'] == 15000
    assert fit['runtimeBackend'] == 'vulkan'
    for change in ('missing-projector','changed-hardware','failed','text-only','auto-fit','missing-drafts'):
        invalid = deepcopy(measured)
        if change == 'missing-projector': invalid['execution']['visionProjectorSha256'] = None
        if change == 'changed-hardware': invalid['hardware']['name'] = 'Unrelated GPU'
        if change == 'failed': invalid['status'] = 'failed'
        if change == 'text-only': invalid['conditions']['visionProjectorLoaded'] = False
        if change == 'auto-fit': invalid['execution']['gpuLayers'] = 'auto'
        if change == 'missing-drafts': invalid['mtp'][0]['acceptedDraftTokens'] = 0
        with pytest.raises(ValueError):
            qualify_memory_fit(invalid, qualified)


def test_verified_profile_exposes_separate_availability_without_claiming_gpu_fit(tmp_path, monkeypatch):
    from performance_oracle import build_models_payload
    from models import GPUInfo, ModelLibraryResponse
    qualified, measured = memory_evidence()
    fit = qualify_memory_fit(measured, qualified)
    data, external = tmp_path/'data', tmp_path/'external'
    data.mkdir(); external.mkdir()
    model = external/'model.gguf'; model.write_bytes(b'fixture')
    projector = external/'mmproj-F16.gguf'; projector.write_bytes(b'vision')
    fit.update(visionProjectorSize=projector.stat().st_size, visionProjectorMtimeNs=projector.stat().st_mtime_ns)
    profile = {'modelSha256':'a'*64,'runtimeSha256':'b'*64,'qualificationSignature':qualified['signature'],
        'contextLength':16384,'draftTokens':2,'backend':'vulkan','mtp':True,'memoryQualification':fit}
    (data/'model-stores.json').write_text(json.dumps({'schemaVersion':1,'stores':[{'id':'ssd','hostPath':str(external),
        'containerPath':'/model-stores/ssd','profiles':{'model.gguf':profile}}]}))
    env = 'LLM_BACKEND=lemonade\nAMD_INFERENCE_LOCATION=host\nAMD_INFERENCE_RUNTIME_MODE=windows-legacy-lemonade\nSYSTEM_RAM_GB=61\n'
    (tmp_path/'.env').write_text(env)
    monkeypatch.setattr('performance_oracle.inspect_gguf', lambda path:{'readable':True,'metadata':{},'context_length':262144})
    gpu = GPUInfo(name='AMD Radeon RX 9070 XT',memory_total_mb=16188,memory_used_mb=1000,memory_percent=6,
        utilization_percent=0,temperature_c=0,gpu_backend='amd')
    catalog = [{'id':'candidate','name':'27B','gguf_file':'model.gguf','gguf_sha256':'a'*64,'size_mb':14253,
        'vram_required_gb':18,'context_length':32768,'max_context_length':262144}]
    def result():
        return ModelLibraryResponse(**build_models_payload(gpu,None,0,tmp_path,catalog=catalog,evidence=[])).model_dump()['models'][0]
    value = result()
    assert value['fitsVram'] is False
    assert value['activationSupport']['available'] is True
    assert value['activationSupport']['contextLength'] == value['contextLength'] == 16384
    (tmp_path/'.env').write_text(env.replace('SYSTEM_RAM_GB=61','SYSTEM_RAM_GB=16'))
    assert result()['activationSupport'] is None
    (tmp_path/'.env').write_text(env.replace('windows-legacy-lemonade','docker-lemonade'))
    assert result()['activationSupport'] is None
    (tmp_path/'.env').write_text(env)
    projector.write_bytes(b'different vision artifact')
    assert result()['activationSupport'] is None
