#!/usr/bin/env python3
"""Untrusted site execution lives only in the short-lived no-network capsule."""

import http.server
import mimetypes
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
from preview_inspection_protocol import (
    CSP,
    Invalid,
    KIND,
    MAX_BUNDLE,
    MAX_RESULT,
    SANDBOX,
    SCOPE,
    canonical,
    failure,
    plan_hash,
    strict_json,
    validate_bundle,
)

# Runs in a Chromium isolated world, not the site's mutable JS global realm.
OBSERVE_ELEMENT = r"""function() {
  const element = this, style = getComputedStyle(element);
  const rects = [...element.getClientRects()].filter(r => r.width > 0 && r.height > 0);
  return {count:1, visible:element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && rects.length > 0,
    display:style.display, visibility:style.visibility, opacity:style.opacity,
    hidden:element.hasAttribute('hidden'), hiddenUntilFound:element.getAttribute('hidden') === 'until-found',
    rectCount:rects.length};
}"""

DIAGNOSTIC = r"""function() {
  let count=0, untilFound=0;
  for (const element of document.querySelectorAll('[hidden]')) {
    if(element.getAttribute('hidden')==='until-found') { untilFound++; continue; }
    if(element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && [...element.getClientRects()].some(r=>r.width>0 && r.height>0)) count++;
  }
  return {renderedHiddenAttributeCount:count, hiddenUntilFoundCount:untilFound};
}"""

# Catch only the browser's selector-parser failure in the isolated world. Do
# not infer syntax from an exception string or rewrite the owner's selector.
SELECTOR_COUNT = r"""function(selector) {
  try { return {count:document.querySelectorAll(selector).length}; }
  catch (error) {
    if (error instanceof DOMException && error.name === 'SyntaxError') return {invalidSelector:true};
    throw error;
  }
}"""


# Chromium's accessibility tree omits hidden elements and computes no name for
# them, so an exact role/name locator could never address the element an
# assert-hidden step expects to be hidden. For assert-hidden only, exact
# role/name matching also includes hidden elements, following Playwright's
# getByRole(role, {name, exact: true, includeHidden: true}) role and name rules
# (script, style, template and noscript text never contributes). It runs in the
# isolated world, so page script cannot replace the DOM or style APIs it reads.
# Chromium's own rendered matches are passed in and kept, so a rendered element
# is matched exactly as before; the union is de-duplicated by identity.
ROLE_NAME_INCLUDING_HIDDEN = r"""function(role, name, ...rendered) {
  const VALID = new Set(('alert alertdialog application article banner blockquote button caption cell checkbox code ' +
    'columnheader combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure ' +
    'form generic grid gridcell group heading img insertion link list listbox listitem log main mark marquee math meter ' +
    'menu menubar menuitem menuitemcheckbox menuitemradio navigation none note option paragraph presentation progressbar ' +
    'radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider spinbutton status strong ' +
    'subscript superscript switch tab table tablist tabpanel term textbox time timer toolbar tooltip tree treegrid treeitem').split(' '));
  const GLOBAL = ('atomic busy controls current describedby details dropeffect flowto grabbed hidden keyshortcuts label ' +
    'labelledby live owns relevant roledescription').split(' ').map(a => 'aria-' + a);
  const CONTENT = new Set(('button cell checkbox columnheader gridcell heading link menuitem menuitemcheckbox menuitemradio ' +
    'option radio row rowheader switch tab tooltip treeitem').split(' '));
  const DESCENDANT = new Set(['', ...('caption code contentinfo definition deletion emphasis insertion list listitem mark none ' +
    'paragraph presentation region row rowgroup section strong subscript superscript table term time').split(' ')]);
  const TAG = {article:'article', aside:'complementary', blockquote:'blockquote', button:'button', caption:'caption', code:'code',
    datalist:'listbox', dd:'definition', del:'deletion', details:'group', dfn:'term', dialog:'dialog', dt:'term', em:'emphasis',
    fieldset:'group', figure:'figure', h1:'heading', h2:'heading', h3:'heading', h4:'heading', h5:'heading', h6:'heading',
    hr:'separator', html:'document', ins:'insertion', li:'listitem', main:'main', mark:'mark', math:'math', menu:'list',
    meter:'meter', nav:'navigation', ol:'list', optgroup:'group', option:'option', output:'status', p:'paragraph',
    progress:'progressbar', search:'search', strong:'strong', sub:'subscript', sup:'superscript', svg:'img', table:'table',
    tbody:'rowgroup', td:'cell', textarea:'textbox', tfoot:'rowgroup', th:'columnheader', thead:'rowgroup', time:'time',
    tr:'row', ul:'list'};
  const INPUT = {button:'button', checkbox:'checkbox', image:'button', number:'spinbutton', radio:'radio', range:'slider',
    reset:'button', submit:'button'};
  const IGNORED = new Set(['script', 'style', 'template', 'noscript']);
  const LANDMARK = 'article:not([role]), aside:not([role]), main:not([role]), nav:not([role]), section:not([role]), ' +
    '[role=article], [role=complementary], [role=main], [role=navigation], [role=region]';
  const tag = e => String(e.localName || '').toLowerCase();
  const style = (e, pseudo) => { try { return getComputedStyle(e, pseudo); } catch { return null; } };
  const idRefs = (e, attribute) => {
    const value = e.getAttribute(attribute), root = e.getRootNode(), out = [];
    for (const id of (value || '').split(' ').filter(Boolean)) {
      let target = null;
      try { target = root.querySelector('#' + CSS.escape(id)); } catch {}
      if (target && !out.includes(target)) out.push(target);
    }
    return out;
  };
  const named = e => e.hasAttribute('aria-label') || e.hasAttribute('aria-labelledby');
  const implicit = e => {
    const t = tag(e);
    if (t === 'a' || t === 'area') return e.hasAttribute('href') ? 'link' : null;
    if (t === 'select') return e.hasAttribute('multiple') || e.size > 1 ? 'listbox' : 'combobox';
    if (t === 'img') return e.getAttribute('alt') === '' && !e.getAttribute('title') && !GLOBAL.some(a => e.hasAttribute(a)) &&
      Number.isNaN(Number(String(e.getAttribute('tabindex')))) ? 'presentation' : 'img';
    if (t === 'header' || t === 'footer') return e.closest(LANDMARK) ? null : t === 'header' ? 'banner' : 'contentinfo';
    if (t === 'form' || t === 'section') return named(e) ? (t === 'form' ? 'form' : 'region') : null;
    if (t === 'input') {
      const type = String(e.type).toLowerCase();
      if (type === 'search') return e.hasAttribute('list') ? 'combobox' : 'searchbox';
      if (['email', 'tel', 'text', 'url', ''].includes(type)) {
        const list = idRefs(e, 'list')[0];
        return list && tag(list) === 'datalist' ? 'combobox' : 'textbox';
      }
      if (type === 'hidden') return null;
      return type === 'file' ? 'button' : INPUT[type] || 'textbox';
    }
    return TAG[t] || null;
  };
  const focusable = e => {
    const t = tag(e);
    if (e.disabled === true) return false;
    const native = ['button', 'details', 'select', 'textarea'].includes(t) ||
      ((t === 'a' || t === 'area') && e.hasAttribute('href')) || (t === 'input' && !e.hidden);
    return native || !Number.isNaN(Number(String(e.getAttribute('tabindex'))));
  };
  const roleOf = e => {
    const explicit = (e.getAttribute('role') || '').split(' ').map(r => r.trim()).find(r => VALID.has(r)) || null;
    if (!explicit) return implicit(e);
    if ((explicit === 'none' || explicit === 'presentation') && (GLOBAL.some(a => e.hasAttribute(a)) || focusable(e)))
      return implicit(e);
    return explicit;
  };
  const unescape = s => s.replace(/\\([0-9a-fA-F]{1,6})\s?|\\([\s\S])/g, (_, hex, ch) => {
    if (!hex) return ch;
    const code = parseInt(hex, 16);
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : '�';
  });
  const cssContent = (e, pseudo) => {
    const s = style(e, pseudo), value = s && s.content;
    if (!value || value === 'none' || value === 'normal' || s.display === 'none' || s.visibility === 'hidden') return undefined;
    const tokens = [], token = /\s*(?:"((?:[^"\\]|\\[\s\S])*)"|'((?:[^'\\]|\\[\s\S])*)'|attr\(\s*([^\s()]+)\s*\)|(\/))\s*/y;
    for (let at = 0; at < value.length;) {
      token.lastIndex = at;
      const m = token.exec(value);
      if (!m) return undefined;
      at = token.lastIndex;
      tokens.push(m[4] ? {slash: true} : m[3] !== undefined ? {text: e.getAttribute(m[3]) || ''} : {text: unescape(m[1] ?? m[2])});
    }
    const slash = tokens.findIndex(t => t.slash);
    if (slash === -1 && !pseudo) return undefined;
    const parts = slash === -1 ? tokens : tokens.slice(slash + 1);
    if (parts.some(t => t.slash)) return undefined;
    const text = parts.map(t => t.text).join('');
    return pseudo && (s.display || 'inline') !== 'inline' ? ' ' + text + ' ' : text;
  };
  const labels = e => { try { return [...(e.labels || [])]; } catch { return []; } };
  const fromLabels = (list, o) =>
    list.map(label => alternative(label, {visited: o.visited, label: true})).filter(Boolean).join(' ');
  const inner = (e, o) => {
    const out = [cssContent(e, '::before') || ''], own = cssContent(e);
    const visit = node => {
      if (node.nodeType === 1) {
        const display = (style(node) || {}).display || 'inline';
        const text = alternative(node, o);
        out.push(display !== 'inline' || node.nodeName === 'BR' ? ' ' + text + ' ' : text);
      } else if (node.nodeType === 3) out.push(node.textContent || '');
    };
    if (own !== undefined) out.push(own);
    else if (tag(e) === 'slot' && e.assignedNodes().length) e.assignedNodes().forEach(visit);
    else {
      for (let child = e.firstChild; child; child = child.nextSibling) if (!child.assignedSlot) visit(child);
      if (e.shadowRoot) for (let child = e.shadowRoot.firstChild; child; child = child.nextSibling) visit(child);
      idRefs(e, 'aria-owns').forEach(visit);
    }
    out.push(cssContent(e, '::after') || '');
    return out.join('');
  };
  function alternative(e, o) {
    const visited = o.visited, t = tag(e);
    if (visited.has(e)) return '';
    if (IGNORED.has(t)) { visited.add(e); return ''; }
    const child = {...o, target: o.target === 'self' ? 'descendant' : o.target};
    const labelledBy = e.hasAttribute('aria-labelledby') ? idRefs(e, 'aria-labelledby') : [];
    if (!o.labelledBy) {
      const text = labelledBy.map(ref => alternative(ref, {visited, labelledBy: true})).join(' ');
      if (text) return text;
    }
    const r = roleOf(e) || '';
    if (o.label || o.labelledBy || o.target === 'descendant') {
      if (r === 'textbox') { visited.add(e); return t === 'input' || t === 'textarea' ? e.value : e.textContent || ''; }
      if (r === 'combobox' || r === 'listbox') {
        visited.add(e);
        if (t !== 'select') return t === 'input' ? e.value : '';
        const selected = [...e.selectedOptions];
        if (!selected.length && e.options.length) selected.push(e.options[0]);
        return selected.map(option => alternative(option, child)).join(' ');
      }
      if (['progressbar', 'scrollbar', 'slider', 'spinbutton', 'meter'].includes(r)) {
        visited.add(e);
        for (const a of ['aria-valuetext', 'aria-valuenow']) if (e.hasAttribute(a)) return e.getAttribute(a) || '';
        return e.getAttribute('value') || '';
      }
      if (r === 'menu') { visited.add(e); return ''; }
    }
    const label = e.getAttribute('aria-label') || '';
    if (label.trim()) { visited.add(e); return label; }
    if (r !== 'presentation' && r !== 'none') {
      const type = t === 'input' ? String(e.type) : '';
      if (['button', 'submit', 'reset'].includes(type)) {
        visited.add(e);
        if ((e.value || '').trim()) return e.value;
        return type === 'submit' ? 'Submit' : type === 'reset' ? 'Reset' : e.getAttribute('title') || '';
      }
      if (type === 'file' || type === 'image') {
        visited.add(e);
        if (labels(e).length && !o.labelledBy) return fromLabels(labels(e), o);
        if (type === 'file') return 'Choose File';
        for (const a of ['alt', 'title']) if ((e.getAttribute(a) || '').trim()) return e.getAttribute(a);
        return 'Submit';
      }
      if (!labelledBy.length && t === 'button') { visited.add(e); if (labels(e).length) return fromLabels(labels(e), o); }
      if (!labelledBy.length && ['textarea', 'select', 'input'].includes(t)) {
        visited.add(e);
        if (labels(e).length) return fromLabels(labels(e), o);
        const placeholder = t === 'textarea' || ['text', 'password', 'search', 'tel', 'email', 'url'].includes(type);
        const title = e.getAttribute('title') || '';
        return !placeholder || title ? title : e.getAttribute('placeholder') || '';
      }
      if (t === 'img' || t === 'area') {
        visited.add(e);
        const alt = e.getAttribute('alt') || '';
        return alt.trim() ? alt : e.getAttribute('title') || '';
      }
    }
    if (CONTENT.has(r) || (o.target === 'descendant' && DESCENDANT.has(r)) || o.labelledBy || o.label ||
        (t === 'summary' && r !== 'presentation' && r !== 'none')) {
      visited.add(e);
      const text = inner(e, child);
      if (o.target === 'self' ? text.trim() : text) return text;
    }
    visited.add(e);
    if (r !== 'presentation' && r !== 'none' || t === 'iframe') {
      const title = e.getAttribute('title') || '';
      if (title.trim()) return title;
    }
    return '';
  }
  const flat = s => s.split(' ').map(c => c.replace(/\r\n/g, '\n').replace(/[​­]/g, '')
    .replace(/\s\s*/g, ' ')).join(' ').trim();
  const normal = s => s.replace(/[​­]/g, '').trim().replace(/\s+/g, ' ');
  const want = normal(name), out = [...new Set(rendered)];
  const walk = root => {
    for (const e of root.querySelectorAll('*')) {
      if (roleOf(e) === role && !out.includes(e) &&
          normal(flat(alternative(e, {visited: new Set(), target: 'self'}))) === want) out.push(e);
      if (e.shadowRoot) walk(e.shadowRoot);
    }
  };
  walk(document);
  return out;
}"""
# Bounds Chromium matches carried into the hidden-inclusive union; more than
# one match already fails uniqueness.
MAX_RENDERED_MATCHES = 32


class InvalidSelector(Invalid):
    pass


# Uncaught page exceptions are author-controlled text. Bound the count, the
# number of distinct messages and each message, and replace every control,
# format, private-use, surrogate or unassigned code point, so the receipt
# carries inert data that cannot restructure the caller's text or its JSON.
MAX_PAGE_ERRORS = 1000
MAX_PAGE_ERROR_MESSAGES = 3
MAX_PAGE_ERROR_CHARS = 200
# Browser automation injects its own URL-less scripts into every document.
# Playwright's service-worker block (kept below) reads navigator.serviceWorker,
# which throws in every opaque-origin preview frame. An exception whose whole
# stack lies in URL-less anonymous code is therefore not attributed to the
# page. Page code runs from its document or script URL; the CSP forbids string
# evaluation. An empty stack (a top-level page exception) is attributed.
INJECTED_FRAME = re.compile(
    r"at (?:async )?(?:<anonymous>|[^()]* \(<anonymous>(?::\d+:\d+)?\))(?::\d+:\d+)?"
)


def injected_only(stack):
    frames = [
        line.strip()
        for line in str(stack or "")[:16384].splitlines()[1:]
        if line.strip().startswith("at ")
    ]
    return bool(frames) and all(INJECTED_FRAME.fullmatch(frame) for frame in frames)


def page_error_text(value):
    try:
        text = str(value)[: 16 * MAX_PAGE_ERROR_CHARS]
    except Exception:
        text = ""
    text = " ".join(
        "".join(
            " "
            if unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in ("Zl", "Zp")
            else c
            for c in text
        ).split()
    )
    if len(text) > MAX_PAGE_ERROR_CHARS:
        text = text[: MAX_PAGE_ERROR_CHARS - 1].rstrip() + "…"
    return text or "(no message)"


class PageErrors:
    """Uncaught exceptions of the inspected page; never evidence of success."""

    def __init__(self):
        self.count = 0
        self.messages = []

    def record(self, error):
        # Playwright delivers this from its event loop. Never let author data
        # raise here: a failed record still counts as an uncaught exception.
        try:
            if injected_only(getattr(error, "stack", "")):
                return
        except Exception:
            pass
        self.count = min(self.count + 1, MAX_PAGE_ERRORS)
        if len(self.messages) >= MAX_PAGE_ERROR_MESSAGES:
            return
        try:
            name = str(getattr(error, "name", "") or "")
            message = str(getattr(error, "message", "") or "")
            raw = (
                f"{name}: {message}"
                if name and message and not message.startswith(name + ":")
                else message or name
            )
        except Exception:
            raw = ""
        text = page_error_text(raw)
        if text not in self.messages:
            self.messages.append(text)

    def receipt(self):
        # Absent means none was observed; older capsules also omit it.
        if not self.count:
            return {}
        return {"pageErrors": {"count": self.count, "messages": list(self.messages)}}


def wrapper_document(prefix):
    # Fixed and script-free: page script exceptions therefore come only from
    # the sandboxed preview frame or a frame the preview itself created.
    return (
        f"<!doctype html><style>html,body{{margin:0;height:100%;overflow:hidden}}iframe{{border:0;width:100%;height:100%}}</style>"
        f'<iframe name="inspection" sandbox="{SANDBOX}" src="{prefix}"></iframe>'
    ).encode()


def observe_until_stable(once, wait):
    # Keep the 100ms fast path. A finite transition may need more samples,
    # but changing observations never become a passing assertion on timeout.
    # The broker's independent 45s capsule deadline still bounds all steps.
    deadline = time.monotonic() + 1.5
    previous = once()
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 0.1:
            return previous, False
        wait(100)
        current = once()
        if time.monotonic() > deadline:
            return current, False
        if current == previous:
            return current, True
        previous = current


def run_browser(bundle, playwright_factory=None):
    request, files = validate_bundle(bundle)
    prefix = "/" + request["siteId"] + "/"
    blocked = []
    page_errors = PageErrors()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/__ods_inspection__.html":
                body = wrapper_document(prefix)
                mime = "text/html"
            elif path.startswith(prefix):
                name = urllib.parse.unquote(path[len(prefix) :]) or "index.html"
                if name not in files:
                    self.send_error(404)
                    return
                body = files[name]
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            else:
                self.send_error(404)
                return
            if path.endswith((".md", ".markdown")):
                mime = "text/plain"
            if mime.startswith("text/") or mime == "application/javascript":
                try:
                    body.decode("utf-8")
                    mime += "; charset=utf-8"
                except UnicodeDecodeError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
            )
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright

        playwright_factory = sync_playwright
    try:
        with playwright_factory() as p:
            # The outer Docker capsule is the mandatory sandbox; this code is
            # never offered as an in-process host browser fallback.
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                viewport=request["viewport"],
                service_workers="block",
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(2000)
            navigation_count = 0

            def route_handler(route):
                nonlocal navigation_count
                req = route.request
                parsed = urllib.parse.urlsplit(req.url)
                safe = (
                    req.method == "GET"
                    and parsed.scheme == "http"
                    and parsed.netloc == f"127.0.0.1:{server.server_port}"
                    and (
                        parsed.path.startswith(prefix)
                        or parsed.path == "/__ods_inspection__.html"
                    )
                )
                if req.is_navigation_request():
                    navigation_count += 1
                    safe = (
                        safe
                        and navigation_count <= 2
                        and req.url
                        in (origin + "/__ods_inspection__.html", origin + prefix)
                    )
                if safe:
                    route.continue_()
                else:
                    if len(blocked) < 32:
                        blocked.append(
                            "navigation" if req.is_navigation_request() else "network"
                        )
                    route.abort()

            context.route("**/*", route_handler)
            context.on(
                "page",
                lambda popup: (
                    blocked.append("popup") if len(blocked) < 32 else None,
                    popup.close(),
                ),
            )
            page.on(
                "download",
                lambda download: (
                    blocked.append("download") if len(blocked) < 32 else None,
                    download.cancel(),
                ),
            )
            page.on(
                "websocket",
                lambda _: blocked.append("websocket") if len(blocked) < 32 else None,
            )
            # Registered before navigation so startup exceptions are included.
            # Page-scoped (not context-wide): blocked popups are never recorded.
            page.on("pageerror", page_errors.record)
            page.goto(
                origin + "/__ods_inspection__.html", wait_until="load", timeout=8000
            )
            frame = page.frame(name="inspection")
            if frame is None or frame.url != origin + prefix:
                raise Invalid("preview frame unavailable")
            # Isolated world prevents author JS overriding querySelector,
            # getComputedStyle, checkVisibility, or receipt construction.
            cdp = context.new_cdp_session(page)
            tree = cdp.send("Page.getFrameTree")["frameTree"]
            frame_id = next(
                child["frame"]["id"]
                for child in tree.get("childFrames", [])
                if child["frame"].get("name") == "inspection"
            )
            world = cdp.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "ods-inspection",
                    "grantUniveralAccess": False,
                },
            )["executionContextId"]

            def evaluate(function, arguments=None):
                if blocked:
                    raise Invalid("preview navigation or request blocked")
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "executionContextId": world,
                        "functionDeclaration": function,
                        "arguments": [{"value": arg} for arg in (arguments or [])],
                        "returnByValue": True,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("invalid selector")
                return result["result"].get("value")

            # Sample in the isolated world; disagreement gets a bounded chance
            # to settle without changing page styles or animation state.
            document_id = cdp.send(
                "Runtime.evaluate", {"expression": "document", "contextId": world}
            )["result"]["objectId"]

            def including_hidden(locator, nodes, owned):
                # Carry Chromium's rendered matches into the isolated world and
                # add hidden-inclusive exact role/name matches by identity.
                unresolved = max(0, len(nodes) - MAX_RENDERED_MATCHES)
                rendered = []
                for n in nodes[:MAX_RENDERED_MATCHES]:
                    try:
                        rendered.append(cdp.send(
                            "DOM.resolveNode",
                            {"backendNodeId": n["backendDOMNodeId"], "executionContextId": world},
                        )["object"]["objectId"])
                    except Exception:
                        # Not in this frame's world (e.g. a nested frame):
                        # still a match for uniqueness, never an observation.
                        unresolved += 1
                owned.extend(rendered)
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "executionContextId": world,
                        "functionDeclaration": ROLE_NAME_INCLUDING_HIDDEN,
                        "arguments": [{"value": locator["role"]}, {"value": locator["name"]},
                                      *({"objectId": h} for h in rendered)],
                        "returnByValue": False,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("inspection failed")
                matches = result["result"]["objectId"]
                owned.append(matches)
                count = unresolved + cdp.send(
                    "Runtime.callFunctionOn",
                    {"objectId": matches, "functionDeclaration": "function(){return this.length}",
                     "returnByValue": True},
                )["result"]["value"]
                if count != 1:
                    return count, None
                if unresolved:
                    raise Invalid("preview element unavailable")
                node = cdp.send(
                    "Runtime.callFunctionOn",
                    {"objectId": matches, "functionDeclaration": "function(){return this[0]}",
                     "returnByValue": False},
                )["result"]["objectId"]
                return 1, node

            def once(locator, include_hidden=False):
                owned = []
                try:
                    return measure(locator, include_hidden, owned)
                finally:
                    for object_id in dict.fromkeys(owned):
                        try:
                            cdp.send("Runtime.releaseObject", {"objectId": object_id})
                        except Exception:
                            pass

            def measure(locator, include_hidden, owned):
                if blocked:
                    raise Invalid("preview navigation or request blocked")
                if "selector" in locator:
                    selection = evaluate(
                        SELECTOR_COUNT,
                        [locator["selector"]],
                    )
                    if selection == {"invalidSelector": True}:
                        raise InvalidSelector("invalid CSS syntax")
                    count = selection["count"]
                    if count != 1:
                        return {"count": count}
                    node = cdp.send(
                        "Runtime.callFunctionOn",
                        {
                            "executionContextId": world,
                            "functionDeclaration": "function(s){return document.querySelector(s)}",
                            "arguments": [{"value": locator["selector"]}],
                            "returnByValue": False,
                        },
                    )["result"]["objectId"]
                else:
                    nodes = cdp.send(
                        "Accessibility.queryAXTree",
                        {
                            "objectId": document_id,
                            "accessibleName": locator["name"],
                            "role": locator["role"],
                        },
                    )["nodes"]
                    nodes = [
                        n
                        for n in nodes
                        if not n.get("ignored")
                        and n.get("role", {}).get("value") == locator["role"]
                        and n.get("name", {}).get("value") == locator["name"]
                        and n.get("backendDOMNodeId")
                    ]
                    if include_hidden:
                        count, node = including_hidden(locator, nodes, owned)
                        if count != 1:
                            return {"count": count}
                    elif len(nodes) != 1:
                        return {"count": len(nodes)}
                    else:
                        node = cdp.send(
                            "DOM.resolveNode",
                            {
                                "backendNodeId": nodes[0]["backendDOMNodeId"],
                                "executionContextId": world,
                            },
                        )["object"]["objectId"]
                owned.append(node)
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": node,
                        "functionDeclaration": OBSERVE_ELEMENT,
                        "returnByValue": True,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("inspection failed")
                return result["result"]["value"]

            def observe(locator, include_hidden=False):
                return observe_until_stable(
                    lambda: once(locator, include_hidden), page.wait_for_timeout
                )

            page.wait_for_timeout(100)
            diagnostics = evaluate(DIAGNOSTIC)
            results = []
            for index, step in enumerate(request["steps"]):
                try:
                    # Only a hidden assertion may address a hidden element by
                    # role/name; assert-visible and click stay rendered-only.
                    before, stable = observe(
                        step["locator"], step["action"] == "assert-hidden"
                    )
                except InvalidSelector:
                    # No DOM observation exists for invalid syntax. Preserve
                    # prior evidence and the exact failing step, then stop.
                    results.append({"index": index, **step, "stable": False,
                                    "status": "failed", "errorCode": "invalid_selector"})
                    break
                item = {
                    "index": index,
                    **step,
                    "before": before,
                    "stable": stable,
                    "status": "failed",
                }
                if before.get("count") == 0:
                    item["errorCode"] = "no_match"
                elif before.get("count") != 1:
                    item["errorCode"] = "selector_not_unique"
                elif not stable:
                    item["errorCode"] = "unstable"
                elif step["action"] == "click":
                    try:
                        (
                            frame.locator("css=" + step["locator"]["selector"])
                            if "selector" in step["locator"]
                            else frame.get_by_role(
                                step["locator"]["role"],
                                name=step["locator"]["name"],
                                exact=True,
                            )
                        ).click(timeout=2000)
                        if blocked:
                            raise Invalid("preview navigation or request blocked")
                        after, after_stable = observe(step["locator"])
                        item.update(
                            after=after,
                            stable=after_stable,
                            status="passed" if after_stable else "failed",
                        )
                    except Exception:
                        item["errorCode"] = "click_failed"
                else:
                    expected = step["action"] == "assert-visible"
                    item["status"] = (
                        "passed" if before.get("visible") is expected else "failed"
                    )
                    if item["status"] == "failed":
                        item["errorCode"] = "visibility_mismatch"
                results.append(item)
                if item["status"] == "failed":
                    break
            # Context is never reused. A click-only receipt proves dispatch,
            # not the post-click condition; callers must assert that condition.
            # Page exceptions are separate evidence: they never change a step
            # or receipt status, and callers must not treat them as verified.
            result = {
                "schemaVersion": 1,
                "kind": KIND,
                "status": "passed"
                if len(results) == len(request["steps"])
                and all(v["status"] == "passed" for v in results)
                and not blocked
                else "failed",
                "siteId": request["siteId"],
                "sha256": request["sha256"],
                "planSha256": plan_hash(request),
                "viewport": request["viewport"],
                "steps": results,
                "diagnostics": diagnostics,
                "blockedRequests": blocked,
                **page_errors.receipt(),
                "scope": SCOPE,
            }
            context.close()
            browser.close()
            return result
    finally:
        server.shutdown()
        server.server_close()


def main():
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_BUNDLE + 1)
        if len(raw) > MAX_BUNDLE:
            raise Invalid("bundle too large")
        bundle = strict_json(raw)
        request, _ = validate_bundle(bundle)
        result = run_browser(bundle)
    except Exception:
        result = failure("unavailable", request)
    encoded = canonical(result)
    if len(encoded) > MAX_RESULT:
        encoded = canonical(failure("output_limit", request))
    sys.stdout.buffer.write(encoded + b"\n")


if __name__ == "__main__":
    main()
