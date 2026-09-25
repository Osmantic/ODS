"""Exact-container custody and real cross-call capsule lifecycle checks."""

import copy
import hashlib
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/host"))
import preview_inspection as broker
import preview_inspection_leases as leases
from preview_inspection_protocol import Invalid
from test_preview_inspection import bundle


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.request = {"schemaVersion":2, "action":"lease", "operation":"close",
            "scope":"a"*64, "leaseId":"b"*32, "containerId":"c"*64}
        self.config = {"imageId":"sha256:"+"d"*64}
        self.info = {"Id":"c"*64, "Image":self.config["imageId"], "Name":"/ods-preview-lease-"+"b"*32,
            "State":{"Running":True}, "Mounts":[],
            "Config":{"User":"65534:65534", "Entrypoint":["python3"],
                "Cmd":["/source/preview_inspection_lease.py","serve"],
                "Labels":{leases.LABEL+"scope":"a"*64,leases.LABEL+"lease":"b"*32}},
            "HostConfig":{"NetworkMode":"none", "ReadonlyRootfs":True, "Privileged":False,
                "CapAdd":None, "CapDrop":["ALL"], "SecurityOpt":["no-new-privileges"],
                "Memory":1024**3, "PidsLimit":128}}

    def test_exact_capability(self):
        self.assertEqual(leases.validate(self.request), self.request)
        self.assertIs(leases.verify_container(self.info,self.config,self.request),self.info)

    def test_wrong_owner_container_image_or_authority_reject(self):
        for section, key, value in [(None,"Id","e"*64),(None,"Image","sha256:"+"f"*64),
            (None,"Mounts",[{}]),("State","Running",False),("HostConfig","NetworkMode","host"),
            ("HostConfig","Privileged",True),("HostConfig","ReadonlyRootfs",False),
            ("HostConfig","CapAdd",["SYS_ADMIN"]),("HostConfig","CapDrop",[]),
            ("HostConfig","SecurityOpt",[]),("Config","User","0:0"),("Config","Cmd",["other"])]:
            info=copy.deepcopy(self.info)
            (info if section is None else info[section])[key]=value
            with self.subTest(section=section,key=key),self.assertRaises(Invalid):
                leases.verify_container(info,self.config,self.request)
        for key in ("scope","lease"):
            info=copy.deepcopy(self.info)
            info["Config"]["Labels"][leases.LABEL+key]="f"*64
            with self.assertRaises(Invalid):leases.verify_container(info,self.config,self.request)

    def test_bad_scope_and_unknown_fields(self):
        for key,value in [("scope","owner-name"),("containerId","--privileged"),("extra",True)]:
            request=dict(self.request,**{key:value})
            with self.assertRaises(Invalid):leases.validate(request)


@unittest.skipUnless(os.environ.get("ODS_PREVIEW_LEASE_IMAGE"), "owned capsule image opt in")
class RealLeaseTests(unittest.TestCase):
    def setUp(self):
        self.data=bundle('<button onclick="document.querySelector(\'#item\').hidden=false">Reveal</button><div id="item" hidden>Target</div>')
        self.config={"imageId":os.environ["ODS_PREVIEW_LEASE_IMAGE"],"docker":"/usr/bin/docker",
            "transport":"local","ownerUid":os.getuid()}
        self.open={"schemaVersion":2,"action":"lease","operation":"open","scope":hashlib.sha256(os.urandom(32)).hexdigest(),
            **{key:self.data["request"][key] for key in ("siteId","sha256","viewport")}}
        with patch.object(broker,"export_bundle",return_value=self.data):
            self.snapshot=leases.handle(self.open,self.config)
        self.cap={"schemaVersion":2,"action":"lease","operation":"close","scope":self.open["scope"],
            "leaseId":self.snapshot["leaseId"],"containerId":self.snapshot["containerId"]}

    def tearDown(self):
        subprocess.run(["docker","rm","-f",self.cap["containerId"]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def test_cross_call_transition_wrong_scope_preserves_owner_and_close_removes(self):
        with self.assertRaises(Invalid):leases.handle(dict(self.cap,scope="f"*64),self.config)
        def ref(name):
            element=next(value for value in self.snapshot["elements"] if value["name"]==name)
            return {"ref":element["ref"],"documentGeneration":self.snapshot["documentGeneration"]}
        request=copy.deepcopy(self.data["request"])
        request["steps"]=[{"action":action,"locator":ref(name)} for action,name in
            [("assert-hidden","Target"),("click","Reveal"),("assert-visible","Target")]]
        result=leases.handle(dict(self.cap,operation="inspect",request=request),self.config)
        self.assertEqual(result["result"]["status"],"passed")
        self.assertEqual(leases.handle(self.cap,self.config)["status"],"closed")
        gone=subprocess.run(["docker","inspect",self.cap["containerId"]],capture_output=True)
        self.assertNotEqual(gone.returncode,0)

    def test_other_publication_rejects_and_does_not_remove_live_document(self):
        different=bundle('<p id="item">Different</p>')["request"]
        with self.assertRaises(Invalid):leases.handle(dict(self.cap,operation="inspect",request=different),self.config)
        self.assertEqual(leases.handle(self.cap,self.config)["status"],"closed")

    def test_cancel_removes_only_the_active_owned_container(self):
        other_open=dict(self.open,scope="e"*64)
        with patch.object(broker,"export_bundle",return_value=self.data):
            other=leases.handle(other_open,self.config)
        other_cap={**self.cap,"scope":other_open["scope"],"leaseId":other["leaseId"],"containerId":other["containerId"]}
        cancelled=threading.Event()
        timer=threading.Timer(0.15,cancelled.set)
        request=copy.deepcopy(self.data["request"])
        request["steps"]=[{"action":"assert-hidden","locator":{"selector":"#item"}}]*12
        try:
            timer.start()
            with self.assertRaises((Invalid,ValueError)):
                leases.handle(dict(self.cap,operation="inspect",request=request),self.config,cancelled)
            gone=subprocess.run(["docker","inspect",self.cap["containerId"]],capture_output=True)
            self.assertNotEqual(gone.returncode,0)
            self.assertEqual(leases.handle(other_cap,self.config)["status"],"closed")
        finally:
            timer.cancel()
            subprocess.run(["docker","rm","-f",other_cap["containerId"]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=="__main__":unittest.main()
