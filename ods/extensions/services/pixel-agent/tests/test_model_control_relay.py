"""The same host/Edge wire contract works without a selected shell or OS path."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_relay as relay

spec=importlib.util.spec_from_file_location('edge_model_contract',ROOT/'extensions/services/pixel-edge/access_mode.py')
edge=importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)
CONFIG={'DASHBOARD_API_KEY':'o'*64,'PIXEL_OPENWEBUI_KEY':'c'*64}
TX='a'*64
REV='b'*64
TARGET=dict(model='Qwen 3.8 (27B)',contextLength=16384,maxTokens=8192,reasoning=True)
STATE=dict(schemaVersion=1,status='held',revision=REV,contract=TARGET,pending=True,transactionId=TX,outcome=None)


class ModelControlTests(unittest.TestCase):
    def test_host_and_edge_validation_share_the_same_contract(self):
        names={'valid_model_contract','_model_hex','valid_model_control','public_model_control'}
        def functions(path):
            return {item.name:ast.dump(item,include_attributes=False) for item in ast.parse(path.read_text()).body
                    if isinstance(item,ast.FunctionDef) and item.name in names}
        self.assertEqual(functions(ROOT/'bin/pixel_access_relay.py'),
                         functions(ROOT/'extensions/services/pixel-edge/access_mode.py'))

    def test_exact_frames_and_response_projection(self):
        valid=[{'operation':'model-status'}, {'operation':'model-begin','request':dict(revision=REV,transactionId=TX)},
               {'operation':'model-apply','request':dict(transactionId=TX,target=TARGET)},
               {'operation':'model-finish','request':dict(transactionId=TX,outcome='rollback')}]
        for value in valid:
            self.assertTrue(relay.valid_model_control(value))
        for target in ({**TARGET,'contextLength':True},{**TARGET,'contextLength':4095},
                       {**TARGET,'maxTokens':20000},{**TARGET,'model':'Qwen\n'},
                       {**TARGET,'routeFingerprint':REV+'\n'},{**TARGET,'path':'/etc'}):
            self.assertFalse(relay.valid_model_control({'operation':'model-apply','request':dict(transactionId=TX,target=target)}))
        self.assertEqual(relay.public_model_control({**STATE,'secret':'private'}),STATE)
        for value in ({**STATE,'pending':False},{**STATE,'transactionId':None},{**STATE,'status':'completed'}):
            with self.assertRaises(ValueError): relay.public_model_control(value)

    def test_platforms_use_the_inspected_container_and_never_shell_or_credential_argv(self):
        for system in ('Windows','Darwin','Linux'):
            with self.subTest(system=system):
                calls=[]
                def run(args,**kwargs):
                    calls.append((args,kwargs))
                    return types.SimpleNamespace(stdout=(('d'*64+' true pixel-edge').encode() if args[1]=='inspect'
                        else json.dumps({'status':200,'body':{**STATE,'private':'secret'}}).encode()))
                with patch.object(relay.platform,'system',return_value=system),patch.object(relay.subprocess,'run',side_effect=run):
                    self.assertEqual(relay.request_runtime_model_control('model-begin',dict(revision=REV,transactionId=TX),config=CONFIG),(200,STATE))
                self.assertEqual(calls[1][0][3],'d'*64)
                self.assertNotIn(CONFIG['DASHBOARD_API_KEY'],' '.join(calls[1][0]))
                self.assertTrue(all('shell' not in options for _,options in calls))
                self.assertEqual(json.loads(calls[1][1]['input'])['path'],'/v1/model-control')
                self.assertEqual('creationflags' in calls[1][1],system=='Windows')

    def test_invalid_frame_or_missing_owner_never_mutates(self):
        with patch.object(relay.subprocess,'run') as run:
            self.assertEqual(relay.request_runtime_model_control('model-finish',dict(transactionId=TX,outcome='release'),config=CONFIG)[0],400)
            with self.assertRaises(relay.AccessRelayError):
                relay.request_runtime_model_control('model-status',config={**CONFIG,'DASHBOARD_API_KEY':CONFIG['PIXEL_OPENWEBUI_KEY']})
            run.assert_not_called()

    def test_lost_reply_is_not_replayed(self):
        with patch.object(relay.platform,'system',return_value='Windows'),patch.object(relay.subprocess,'run',side_effect=[
            types.SimpleNamespace(stdout=('d'*64+' true pixel-edge').encode()),subprocess.TimeoutExpired('docker',313)]) as run:
            with self.assertRaises(relay.AccessRelayError):
                relay.request_runtime_model_control('model-apply',dict(transactionId=TX,target=TARGET),config=CONFIG)
            self.assertEqual(run.call_count,2)


if __name__=='__main__': unittest.main()
