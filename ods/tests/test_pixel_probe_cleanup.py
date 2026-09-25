import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

spec=importlib.util.spec_from_file_location('cleanup',Path(__file__).parents[1]/'scripts/pixel_probe_cleanup.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@unittest.skipUnless(os.name=='posix','owner-private POSIX cleanup')
class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.scope=str(uuid.uuid4())
        self.evidence={'settlement':'a'*64,'routerDisarm':'b'*64}
        self.name='authorize-'+'c'*64+'.json'
        self.file=self.root/self.name
        self.file.write_text(json.dumps({'scopeId':self.scope,'signingKey':'fixture-only'}))
        self.file.chmod(0o600)
    def tearDown(self):self.temp.cleanup()
    def manifest(self,names=None):return m.create_manifest(self.root,self.scope,names or [self.name],self.evidence)
    def clean(self,value,**kw):return m.cleanup(value,approved_manifest_sha256=m.digest(value),evidence=self.evidence,**kw)
    def test_actual_owned_cleanup_and_replay_record_absence_without_inventing_removal(self):
        value=self.manifest()
        self.assertTrue(self.clean(value)['allManifestNamesAbsent'])
        again=self.clean(value)
        self.assertEqual(again['files'][0]['state'],'absent-unattributed')
    def test_crash_two_links_and_partial_cleanup_reconcile_exact_remaining_names(self):
        claim=self.name+'.claimed'
        os.link(self.file,self.root/claim)
        with self.assertRaises(ValueError):self.manifest()
        value=self.manifest([self.name,claim])
        with self.assertRaises(RuntimeError):self.clean(value,after_unlink=lambda _:(_ for _ in ()).throw(RuntimeError('fixture crash')))
        result=self.clean(value)
        self.assertTrue(result['allManifestNamesAbsent'])
        self.assertEqual(result['files'][0]['state'],'absent-unattributed')
    def test_changed_replacement_foreign_scope_symlink_and_unapproved_manifest_refused(self):
        value=self.manifest()
        self.file.write_text(json.dumps({'scopeId':self.scope,'changed':True}))
        with self.assertRaises(ValueError):self.clean(value)
        self.assertTrue(self.file.exists())
        self.file.write_text(json.dumps({'scopeId':str(uuid.uuid4())}))
        with self.assertRaises(ValueError):self.manifest()
        self.file.unlink()
        self.file.symlink_to(self.root/'missing')
        with self.assertRaises(OSError):self.manifest()
        with self.assertRaises(ValueError):m.cleanup(value,approved_manifest_sha256='0'*64,evidence=self.evidence)
    def test_nonmanifest_files_survive_and_evidence_change_refused(self):
        other=self.root/'original-failure-receipt.json'
        other.write_text('{}')
        value=self.manifest()
        with self.assertRaises(ValueError):m.cleanup(value,approved_manifest_sha256=m.digest(value),evidence={**self.evidence,'routerDisarm':'d'*64})
        self.assertTrue(self.clean(value)['allManifestNamesAbsent'])
        self.assertTrue(other.exists())
