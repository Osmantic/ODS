import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('pixel_measurement', Path(__file__).parents[1]/'scripts/pixel_measurement.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MeasurementTests(unittest.TestCase):
    def test_six_counterbalanced_blocks_preserve_task_and_checkpoint(self):
        identities={k:'a'*64 for k in ['source','runtime','model','serving','capabilities','oracle']}
        task={'prompt':'Keep source bytes unchanged.'}
        checkpoint={'files':[{'path':'a.py','sha256':'b'*64,'mode':420}], 'history':'c'*64}
        value=m.paired_schedule(42,task,checkpoint,identities,'combined')
        self.assertEqual(value,m.paired_schedule(42,task,checkpoint,identities,'combined'))
        self.assertEqual(sum(r['arms'][0]=='baseline' for r in value['blocks']),3)
        self.assertEqual(len(value['blocks']),6)
        self.assertTrue(all(r['taskSha256']==m.digest(task) and r['checkpointSha256']==m.digest(checkpoint) and not r['promptMutation'] for r in value['blocks']))
        self.assertFalse(value['launchAuthorized'])
        self.assertTrue(all(r['cacheCondition']=='unverified' for r in value['blocks']))

    def test_missing_identities_rejected(self):
        with self.assertRaises(ValueError):m.paired_schedule(1,{}, {}, {},'combined')

    def test_joins_repairs_without_inventing_queue_or_visible_timing(self):
        native=[{'correlationId':'owned','modelCallId':'run:model:1','attempt':1}]
        router=[{'binding':'signed-http-header','correlationId':'owned','attempt':i,'elapsedMs':10} for i in [1,2]]
        rows=m.join_attempts(native,router)
        self.assertEqual([r['modelCallId'] for r in rows],['run:model:1']*2)
        self.assertEqual([r['routerSendAttempt'] for r in rows],[1,2])
        self.assertTrue(all(r['backendQueueMs'] is None and r['usefulVisibleProgressMs'] is None for r in rows))
        self.assertEqual(m.join_attempts([],router)[0]['join'],'unavailable')
        with self.assertRaises(ValueError):m.join_attempts(native+native,router)

    def test_component_difference_not_token_offset_and_requires_scope(self):
        a={'state':'observed','components':{'tools':{'digest':'x','entries':[]},'messages':{'digest':'a','entries':[{'message':'a'}]}}}
        b={'state':'observed','components':{'tools':{'digest':'x','entries':[]},'messages':{'digest':'b','entries':[{'message':'b'}]}}}
        self.assertEqual(m.first_component_difference(a,b,same_scope=True),{'state':'different','component':'messages','index':0,'tokenOffset':None})
        self.assertEqual(m.first_component_difference(a,b,same_scope=False)['state'],'unavailable')

    def test_checkpoint_refuses_escape_duplicate_missing_modes_and_placeholder_history(self):
        identities={k:'a'*64 for k in ['source','runtime','model','serving','capabilities','oracle']}
        entry={'path':'source/a.py','sha256':'b'*64,'mode':420}
        for files in [[dict(entry,path='../a')],[dict(entry,path='/a')],[dict(entry,path='a\\b')],[entry,entry],[{'path':'a','sha256':'b'*64}],[dict(entry,mode=True)],[dict(entry,mode=0o4000)]]:
            with self.subTest(files=files),self.assertRaises(ValueError):
                m.paired_schedule(1,{'prompt':'same'},{'files':files,'history':'c'*64},identities,'combined')
        with self.assertRaises(ValueError):m.paired_schedule(1,{'prompt':'same'},{'files':[],'history':'unknown'},identities,'combined')
