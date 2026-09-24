"""Boundaries and opt-in real browser regressions (ODS_PREVIEW_BROWSER_TESTS=1)."""
import base64, copy, hashlib, os, socket, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'extensions/services/pixel-agent/host'))
import preview_inspection_protocol as protocol
import preview_inspection as broker
import preview_inspection_capsule as capsule

def step(action, selector=None, name=None):
    return {'action':action,'locator':{'selector':selector} if selector is not None else {'role':'button','name':name,'exact':True}}
def bundle(html,steps=None):
    data=html.encode();name=b'index.html';digest=hashlib.sha256(len(name).to_bytes(4,'big')+name+len(data).to_bytes(8,'big')+data).hexdigest()
    return {'schemaVersion':1,'request':{'schemaVersion':1,'action':'inspect','siteId':'site-'+digest[:24],'sha256':digest,'viewport':{'width':375,'height':812},'steps':steps or [step('assert-visible','#item')]},'files':[{'path':'index.html','base64':base64.b64encode(data).decode()}]}
class ProtocolTests(unittest.TestCase):
    def test_production_frame_contract(self):
        import workspace_preview as publisher
        self.assertEqual(protocol.CSP,publisher.CSP)
        dashboard=(Path(__file__).resolve().parents[1]/'extensions/services/dashboard/src/pages/Pixel.jsx').read_text()
        self.assertIn("sandbox: '"+protocol.SANDBOX+"'",dashboard)
    def test_binding(self):
        good=bundle('hi');protocol.validate_bundle(good)
        for mutation in ('hash','bytes','path','duplicate','unknown'):
            bad=copy.deepcopy(good)
            if mutation=='hash':bad['request'].update(sha256='0'*64,siteId='site-'+'0'*24)
            elif mutation=='bytes':bad['files'][0]['base64']='eA=='
            elif mutation=='path':bad['files'][0]['path']='../index.html'
            elif mutation=='duplicate':bad['files']*=2
            else:bad['files'][0]['url']='http://localhost'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):protocol.validate_bundle(bad)
    def test_no_script_url_unbounded_locator(self):
        for locator in ({'selector':'xpath=//*'},{'selector':'a >> iframe'},{'selector':'x'*257},{'role':'button','name':'ok','exact':False},{'role':'arbitrary','name':'ok','exact':True},{'url':'http://localhost'},{'eval':'process.exit()'}):
            request=bundle('hi')['request'];request['steps']=[{'action':'click','locator':locator}]
            with self.subTest(locator=locator),self.assertRaises(ValueError):protocol.validate_request(request)
        request=bundle('hi')['request'];request['steps']*=13
        with self.assertRaises(ValueError):protocol.validate_request(request)
        with self.assertRaises(ValueError):protocol.strict_json('{"a":1,"a":2}')
    def test_isolation(self):
        config={'docker':'/usr/bin/docker','imageId':'sha256:'+'a'*64,'transport':'local'}
        argv=broker.capsule_argv(config,'ods-preview-inspection-owned')
        for flag in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pull=never','--memory=1g','--pids-limit=128'):self.assertIn(flag,argv)
        for flag in ('-v','--volume','--mount','--env','--privileged','--network=host'):self.assertNotIn(flag,argv)
        self.assertEqual(argv[:3],['/usr/bin/docker','--host','unix:///var/run/docker.sock'])
    def test_timeout_output_and_cancel(self):
        start=time.monotonic()
        with self.assertRaisesRegex(ValueError,'timeout'):broker.bounded_process([sys.executable,'-c','import time;time.sleep(30)'],b'',timeout=.1,limit=100)
        self.assertLess(time.monotonic()-start,3)
        with self.assertRaisesRegex(ValueError,'output_limit'):broker.bounded_process([sys.executable,'-c','print("x"*10000)'],b'',timeout=2,limit=100)
        import threading
        cancelled=threading.Event();cancelled.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):broker.bounded_process([sys.executable,'-c','import time;time.sleep(30)'],b'',timeout=2,limit=100,cancelled=cancelled)
    def test_container_cleanup(self):
        request=bundle('hi')['request'];config={'docker':'/usr/bin/docker','imageId':'sha256:'+'a'*64,'ownerUid':os.getuid(),'transport':'local','snapshotRoot':'/owned'};calls=[]
        def process(argv,*args,**kwargs):
            calls.append(argv)
            if 'run' in argv:raise protocol.Invalid('cancelled')
            return b''
        with patch.object(broker,'snapshot_bundle',return_value=bundle('hi')),patch.object(broker,'bounded_process',side_effect=process):
            with self.assertRaisesRegex(ValueError,'cancelled'):broker.inspect_request(request,config)
        self.assertEqual(calls[1][3:5],['rm','-f']);self.assertEqual(calls[1][5],calls[0][calls[0].index('--name')+1])
    def test_peer(self):
        a,b=socket.socketpair()
        try:
            with patch('unix_peer.peer_ids',return_value=(123,123)),patch.object(broker,'inspect_request') as run:
                broker.handle(a,{'ownerUid':456});self.assertFalse(run.called);self.assertEqual(protocol.strict_json(b.recv(4096))['status'],'failed')
        finally:a.close();b.close()
    def test_snapshot_mode_symlink(self):
        import workspace_preview as publisher
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir(mode=0o700);site=workspace/'site';site.mkdir(mode=0o700)
            (site/'index.html').write_text('<p id="item">hi</p>');(site/'index.html').chmod(0o600)
            previews=root/'previews';previews.mkdir(mode=0o700);receipt=publisher.publish_snapshot(workspace,previews,'site',os.getuid())
            request=bundle('hi')['request'];request.update(siteId=receipt['siteId'],sha256=receipt['sha256'])
            protocol.validate_bundle(broker.snapshot_bundle(previews,request))
            target=previews/receipt['siteId']/'index.html';target.chmod(0o600)
            with self.assertRaises(ValueError):broker.snapshot_bundle(previews,request)
            target.parent.chmod(0o700);target.unlink();target.symlink_to(site/'index.html')
            with self.assertRaises(publisher.PreviewError):broker.snapshot_bundle(previews,request)
@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS')=='1','real Chromium opt in')
class BrowserTests(unittest.TestCase):
    def check(self,html,steps):
        # Fixture browsers get a separate process group and deadline too. The
        # production caller uses the stricter Docker capsule, never this path.
        import subprocess, signal
        script = str(Path(capsule.__file__).resolve())
        child=subprocess.Popen([sys.executable,script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            output,error=child.communicate(protocol.canonical(bundle(html,steps)),timeout=20)
            self.assertEqual(child.returncode,0,error.decode(errors='replace'))
            return protocol.strict_json(output)
        finally:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait(timeout=5)

    def test_hidden_flex(self):
        html='<style>.card{display:flex}</style><article hidden class="card" id="item">Sold out</article>'
        result=self.check(html,[step('assert-hidden','#item')]);self.assertEqual(result['status'],'failed');self.assertTrue(result['steps'][0]['before']['visible']);self.assertEqual(result['diagnostics']['renderedHiddenAttributeCount'],1)
        html+='<style>.card[hidden]{display:none}</style><button onclick="document.querySelector(\'#item\').hidden=false">Reveal</button>'
        result=self.check(html,[step('assert-hidden','#item'),step('click',name='Reveal'),step('assert-visible','#item')]);self.assertEqual(result['status'],'passed',result)
    def test_accessible_name_prefix(self):
        html='<style>button::before{content:"\\2193 "}</style><button>Show items</button>'
        result=self.check(html,[step('click',name='Show items')]);self.assertEqual(result['status'],'failed');self.assertEqual(result['steps'][0]['before']['count'],0)
        result=self.check(html.replace('<button>','<button aria-label="Show items">'),[step('click',name='Show items')]);self.assertEqual(result['status'],'passed',result)
    def test_unicode_accessible_name(self):
        result=self.check('<button>Mostrar pr\u00f3ximos eventos</button>',[step('click',name='Mostrar pr\u00f3ximos eventos')])
        self.assertEqual(result['status'],'passed',result)
    def test_visibility_scope(self):
        result=self.check('<div id="item" style="opacity:0">hidden</div>',[step('assert-hidden','#item')]);self.assertEqual(result['status'],'passed')
        for style in ('clip-path:inset(100%)','position:absolute;left:-10000px',''):
            result=self.check(f'<div id="item" style="{style}">layout visible</div><div style="position:fixed;inset:0;background:black"></div>',[step('assert-visible','#item')]);self.assertEqual(result['status'],'passed',result);self.assertIn('not pixel paint, occlusion, clipping',result['scope'])
    def test_unknown_selector(self):
        result=self.check('<p>empty</p>',[step('assert-hidden','#missing')]);self.assertEqual(result['status'],'failed');self.assertEqual(result['steps'][0]['errorCode'],'selector_not_unique')
    def test_spoofing(self):
        html='<p id="item" style="display:none">hidden</p><script>window.getComputedStyle=()=>({display:"block"});Element.prototype.checkVisibility=()=>true;Document.prototype.querySelectorAll=()=>[document.body];</script>'
        result=self.check(html,[step('assert-hidden','#item')]);self.assertEqual(result['status'],'passed',result)
    def test_external_network_never_reaches_listener(self):
        import http.server, threading
        hits=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                hits.append(self.path);self.send_response(200);self.end_headers()
        server=http.server.HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            html=f'<p id="item">safe</p><script>fetch("http://127.0.0.1:{server.server_port}/secret").catch(()=>{{}})</script>'
            result=self.check(html,[step('assert-visible','#item')])
            self.assertEqual(hits,[])
            self.assertEqual(result['steps'][0]['status'],'passed')
        finally:server.shutdown();server.server_close()
    def test_intentional_hidden_override_diagnostic_only(self):
        result=self.check('<p hidden id="item" style="display:block">intentionally visible</p>',[step('assert-visible','#item')])
        self.assertEqual(result['status'],'passed');self.assertEqual(result['diagnostics']['renderedHiddenAttributeCount'],1)
    def test_navigation(self):
        result=self.check('<button onclick="location.href=\'next.html\'">Next</button>',[step('click',name='Next')]);self.assertEqual(result['status'],'failed');self.assertIn('navigation',result['blockedRequests'])

@unittest.skipUnless(os.environ.get('ODS_INSPECTION_TEST_IMAGE'),'isolated Docker image test opt in')
class DockerCapsuleTests(unittest.TestCase):
    def invoke(self, html, steps, cancel=False):
        import workspace_preview as publisher
        import threading, subprocess
        image=os.environ['ODS_INSPECTION_TEST_IMAGE']
        self.assertRegex(image,r'^sha256:[a-f0-9]{64}$')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';workspace.mkdir(mode=0o700);site=workspace/'site';site.mkdir(mode=0o700)
            (site/'index.html').write_text(html);(site/'index.html').chmod(0o600)
            previews=root/'previews';previews.mkdir(mode=0o700);receipt=publisher.publish_snapshot(workspace,previews,'site',os.getuid())
            request=bundle(html,steps)['request'];request.update(siteId=receipt['siteId'],sha256=receipt['sha256'])
            config={'docker':'/usr/bin/docker','imageId':image,'ownerUid':os.getuid(),'transport':'local','snapshotRoot':str(previews)}
            names=[];real=broker.capsule_argv
            def argv(cfg,name):names.append(name);return real(cfg,name)
            event=threading.Event()
            if cancel:
                timer=threading.Timer(.5,event.set);timer.start()
            try:
                with patch.object(broker,'capsule_argv',side_effect=argv):
                    if cancel:
                        with self.assertRaisesRegex(ValueError,'cancelled'):broker.inspect_request(request,config,event)
                        result=None
                    else:result=broker.inspect_request(request,config,event)
            finally:
                if cancel:timer.cancel()
                for name in names:
                    remaining=subprocess.check_output([*broker.docker_prefix(config),'ps','-aq','--filter','name=^/'+name+'$'],text=True)
                    self.assertEqual(remaining.strip(),'','owned capsule survived cleanup')
            return result
    def test_real_capsule_toggle(self):
        html='<p id="item" hidden>hidden</p><button onclick="document.querySelector(\'#item\').hidden=false">Reveal</button>'
        result=self.invoke(html,[step('assert-hidden','#item'),step('click',name='Reveal'),step('assert-visible','#item')])
        self.assertEqual(result['status'],'passed',result)
    def test_real_capsule_observed_bug(self):
        result=self.invoke('<style>.card{display:flex}</style><p id="item" class="card" hidden>hidden</p>',[step('assert-hidden','#item')])
        self.assertEqual(result['status'],'failed');self.assertEqual(result['steps'][0]['errorCode'],'visibility_mismatch')
    def test_real_capsule_hung_script(self):
        start=time.monotonic();result=self.invoke('<script>while(true){}</script>',[step('assert-visible','body')])
        self.assertEqual(result['status'],'failed');self.assertLess(time.monotonic()-start,55)
    def test_real_capsule_cancel_cleanup(self):
        self.invoke('<script>while(true){}</script>',[step('assert-visible','body')],cancel=True)

if __name__=='__main__':unittest.main()
