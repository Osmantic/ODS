#!/usr/bin/env python3
"""Untrusted site execution lives only in the short-lived no-network capsule."""
import hashlib
import http.server
import json
import mimetypes
import sys
import threading
import urllib.parse
from preview_inspection_protocol import *

# Runs in a Chromium isolated world, not the site's mutable JS global realm.
OBSERVE_ELEMENT = r'''function() {
  const element = this, style = getComputedStyle(element);
  const rects = [...element.getClientRects()].filter(r => r.width > 0 && r.height > 0);
  return {count:1, visible:element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && rects.length > 0,
    display:style.display, visibility:style.visibility, opacity:style.opacity,
    hidden:element.hasAttribute('hidden'), hiddenUntilFound:element.getAttribute('hidden') === 'until-found',
    rectCount:rects.length};
}'''

DIAGNOSTIC = r'''function() {
  let count=0, untilFound=0;
  for (const element of document.querySelectorAll('[hidden]')) {
    if(element.getAttribute('hidden')==='until-found') { untilFound++; continue; }
    if(element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && [...element.getClientRects()].some(r=>r.width>0 && r.height>0)) count++;
  }
  return {renderedHiddenAttributeCount:count, hiddenUntilFoundCount:untilFound};
}'''

def run_browser(bundle, playwright_factory=None):
    request, files = validate_bundle(bundle)
    prefix = '/' + request['siteId'] + '/'
    blocked = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/__ods_inspection__.html':
                body = (f'<!doctype html><style>html,body{{margin:0;height:100%;overflow:hidden}}iframe{{border:0;width:100%;height:100%}}</style>'
                        f'<iframe name="inspection" sandbox="{SANDBOX}" src="{prefix}"></iframe>').encode()
                mime = 'text/html'
            elif path.startswith(prefix):
                name = urllib.parse.unquote(path[len(prefix):]) or 'index.html'
                if name not in files:
                    self.send_error(404); return
                body = files[name]
                mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
            else:
                self.send_error(404); return
            if path.endswith(('.md','.markdown')):
                mime = 'text/plain'
            if mime.startswith('text/') or mime == 'application/javascript':
                try:
                    body.decode('utf-8'); mime += '; charset=utf-8'
                except UnicodeDecodeError:
                    pass
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cross-Origin-Opener-Policy','same-origin')
            self.send_header('Cross-Origin-Resource-Policy','cross-origin')
            self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(body)
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright
        playwright_factory = sync_playwright
    try:
        with playwright_factory() as p:
            # The outer Docker capsule is the mandatory sandbox; this code is
            # never offered as an in-process host browser fallback.
            browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
            context = browser.new_context(viewport=request['viewport'], service_workers='block', accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(2000)
            navigation_count = 0
            def route_handler(route):
                nonlocal navigation_count
                req = route.request
                parsed = urllib.parse.urlsplit(req.url)
                safe = req.method == 'GET' and parsed.scheme == 'http' and parsed.netloc == f'127.0.0.1:{server.server_port}' and (parsed.path.startswith(prefix) or parsed.path == '/__ods_inspection__.html')
                if req.is_navigation_request():
                    navigation_count += 1
                    safe = safe and navigation_count <= 2 and req.url in (origin + '/__ods_inspection__.html', origin + prefix)
                if safe:
                    route.continue_()
                else:
                    if len(blocked) < 32:
                        blocked.append('navigation' if req.is_navigation_request() else 'network')
                    route.abort()
            context.route('**/*', route_handler)
            context.on('page', lambda popup: (blocked.append('popup') if len(blocked) < 32 else None, popup.close()))
            page.on('download', lambda download: (blocked.append('download') if len(blocked) < 32 else None, download.cancel()))
            page.on('websocket', lambda _: blocked.append('websocket') if len(blocked) < 32 else None)
            page.goto(origin + '/__ods_inspection__.html', wait_until='load', timeout=8000)
            frame = page.frame(name='inspection')
            if frame is None or frame.url != origin + prefix:
                raise Invalid('preview frame unavailable')
            # Isolated world prevents author JS overriding querySelector,
            # getComputedStyle, checkVisibility, or receipt construction.
            cdp = context.new_cdp_session(page)
            tree = cdp.send('Page.getFrameTree')['frameTree']
            frame_id = next(child['frame']['id'] for child in tree.get('childFrames',[]) if child['frame'].get('name') == 'inspection')
            world = cdp.send('Page.createIsolatedWorld', {'frameId':frame_id,'worldName':'ods-inspection','grantUniveralAccess':False})['executionContextId']
            def evaluate(function, arguments=None):
                if blocked:
                    raise Invalid('preview navigation or request blocked')
                result = cdp.send('Runtime.callFunctionOn', {'executionContextId':world,'functionDeclaration':function,'arguments':[{'value':arg} for arg in (arguments or [])],'returnByValue':True})
                if result.get('exceptionDetails'):
                    raise Invalid('invalid selector')
                return result['result'].get('value')
            # Small stable sampling window; animation/mutation disagreement is
            # inconclusive, never an automatically green assertion.
            document_id = cdp.send('Runtime.evaluate', {'expression':'document','contextId':world})['result']['objectId']
            def once(locator):
                if blocked:
                    raise Invalid('preview navigation or request blocked')
                if 'selector' in locator:
                    count = evaluate('function(s){return document.querySelectorAll(s).length}', [locator['selector']])
                    if count != 1:
                        return {'count':count}
                    node = cdp.send('Runtime.callFunctionOn', {'executionContextId':world,'functionDeclaration':'function(s){return document.querySelector(s)}','arguments':[{'value':locator['selector']}],'returnByValue':False})['result']['objectId']
                else:
                    nodes = cdp.send('Accessibility.queryAXTree', {'objectId':document_id,'accessibleName':locator['name'],'role':locator['role']})['nodes']
                    nodes = [n for n in nodes if not n.get('ignored') and n.get('role',{}).get('value')==locator['role'] and n.get('name',{}).get('value')==locator['name'] and n.get('backendDOMNodeId')]
                    if len(nodes) != 1:
                        return {'count':len(nodes)}
                    node = cdp.send('DOM.resolveNode', {'backendNodeId':nodes[0]['backendDOMNodeId'],'executionContextId':world})['object']['objectId']
                try:
                    result = cdp.send('Runtime.callFunctionOn', {'objectId':node,'functionDeclaration':OBSERVE_ELEMENT,'returnByValue':True})
                    if result.get('exceptionDetails'):
                        raise Invalid('inspection failed')
                    return result['result']['value']
                finally:
                    cdp.send('Runtime.releaseObject', {'objectId':node})
            def observe(locator):
                first = once(locator); page.wait_for_timeout(100)
                second = once(locator)
                return second, first == second
            page.wait_for_timeout(100)
            diagnostics = evaluate(DIAGNOSTIC)
            results = []
            for index, step in enumerate(request['steps']):
                before, stable = observe(step['locator'])
                item = {'index':index, **step, 'before':before, 'stable':stable, 'status':'failed'}
                if before.get('count') != 1:
                    item['errorCode'] = 'selector_not_unique'
                elif not stable:
                    item['errorCode'] = 'unstable'
                elif step['action'] == 'click':
                    try:
                        (frame.locator('css=' + step['locator']['selector']) if 'selector' in step['locator'] else frame.get_by_role(step['locator']['role'],name=step['locator']['name'],exact=True)).click(timeout=2000)
                        if blocked:
                            raise Invalid('preview navigation or request blocked')
                        after, after_stable = observe(step['locator'])
                        item.update(after=after, stable=after_stable, status='passed' if after_stable else 'failed')
                    except Exception:
                        item['errorCode'] = 'click_failed'
                else:
                    expected = step['action'] == 'assert-visible'
                    item['status'] = 'passed' if before.get('visible') is expected else 'failed'
                    if item['status'] == 'failed':
                        item['errorCode'] = 'visibility_mismatch'
                results.append(item)
                if item['status'] == 'failed':
                    break
            # Context is never reused. A click-only receipt proves dispatch,
            # not the post-click condition; callers must assert that condition.
            result = {'schemaVersion':1,'kind':KIND,'status':'passed' if len(results)==len(request['steps']) and all(v['status']=='passed' for v in results) and not blocked else 'failed',
                      'siteId':request['siteId'],'sha256':request['sha256'],'planSha256':plan_hash(request),
                      'viewport':request['viewport'],'steps':results,'diagnostics':diagnostics,
                      'blockedRequests':blocked,'scope':SCOPE}
            context.close(); browser.close()
            return result
    finally:
        server.shutdown(); server.server_close()

def main():
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_BUNDLE + 1)
        if len(raw) > MAX_BUNDLE:
            raise Invalid('bundle too large')
        bundle = strict_json(raw)
        request, _ = validate_bundle(bundle)
        result = run_browser(bundle)
    except Exception:
        result = failure('unavailable', request)
    encoded = canonical(result)
    if len(encoded) > MAX_RESULT:
        encoded = canonical(failure('output_limit', request))
    sys.stdout.buffer.write(encoded + b'\n')

if __name__ == '__main__':
    main()
