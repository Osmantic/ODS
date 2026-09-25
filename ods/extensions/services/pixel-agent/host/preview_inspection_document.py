"""Ephemeral CDP element capabilities; never resolves a reference as a selector."""

import secrets
import time

from preview_inspection_protocol import Invalid, canonical


class DocumentReferences:
    MAX_ELEMENTS = 128

    def __init__(self, cdp, world, document, *, lifetime=120, clock=time.monotonic):
        self.cdp, self.world, self.document = cdp, world, document
        self.clock, self.deadline = clock, clock() + lifetime
        self.generation = secrets.token_hex(16)
        self.nodes = {}
        self.closed = False

    def call(self, node, function, arguments=(), *, by_value=True):
        result = self.cdp.send("Runtime.callFunctionOn", {
            "objectId": node, "functionDeclaration": function,
            "arguments": [{"value": value} for value in arguments],
            "returnByValue": by_value,
        })
        if result.get("exceptionDetails"):
            raise Invalid("document reference unavailable")
        return result["result"].get("value") if by_value else result["result"]

    def current(self):
        if self.closed or self.clock() >= self.deadline:
            raise Invalid("document reference expired")
        try:
            if self.call(self.document, "function(){return this===document}") is not True:
                raise Invalid("document generation changed")
        except Exception as error:
            self.close()
            raise Invalid("document generation unavailable") from error

    def resolve(self, reference, generation):
        self.current()
        if generation != self.generation or reference not in self.nodes:
            raise Invalid("unknown document reference")
        node = self.nodes[reference]
        if self.call(node, "function(){return this.isConnected && this.ownerDocument===document}") is not True:
            raise Invalid("detached document reference")
        return node

    def snapshot(self):
        self.current()
        if self.nodes:
            raise Invalid("document snapshot already issued")
        # Fixed code in the isolated world. Page text is merely bounded data.
        array = self.call(self.document, """function(limit) {
          const nodes=document.querySelectorAll('[id],a,button,input,select,textarea,[role],h1,h2,h3,h4,h5,h6');
          const out=[]; for(let i=0;i<Math.min(nodes.length,limit);i++) out.push(nodes[i]); return out;
        }""", [self.MAX_ELEMENTS], by_value=False)["objectId"]
        entries, size, truncated = [], 0, False
        try:
            values = self.cdp.send("Runtime.getProperties", {"objectId": array, "ownProperties": True})["result"]
            for value in values:
                if not value.get("name", "").isdigit():
                    continue
                node = value.get("value", {}).get("objectId")
                if not node or len(entries) >= self.MAX_ELEMENTS:
                    raise Invalid("invalid snapshot node")
                description = self.call(node, """function(){return {
                  tag:this.localName.slice(0,32), role:(this.getAttribute('role')||'').slice(0,40),
                  name:(this.getAttribute('aria-label')||this.textContent||'').trim().slice(0,160)
                }}""")
                reference = secrets.token_hex(16)
                entry = {"ref": reference, **description}
                size += len(canonical(entry))
                if size > 24000:
                    truncated = True
                    self.cdp.send("Runtime.releaseObject", {"objectId": node})
                    continue
                self.nodes[reference] = node
                entries.append(entry)
            return {"documentGeneration": self.generation, "elements": entries,
                    "limit": self.MAX_ELEMENTS, "boundedSnapshot": True,
                    "truncatedByBytes": truncated, "descriptionsAreUntrusted": True}
        finally:
            self.cdp.send("Runtime.releaseObject", {"objectId": array})

    def observe(self, reference, generation, function):
        return self.call(self.resolve(reference, generation), function)

    def click(self, reference, generation, viewport):
        node = self.resolve(reference, generation)
        self.cdp.send("DOM.scrollIntoViewIfNeeded", {"objectId": node})
        quads = self.cdp.send("DOM.getContentQuads", {"objectId": node}).get("quads", [])
        for quad in quads:
            x, y = sum(quad[0::2]) / 4, sum(quad[1::2]) / 4
            if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
                continue
            # Check hit targeting without mutating attributes or synthesizing
            # author JS click() calls. Input dispatch is Chromium's real path.
            hit = self.call(node, """function(x,y){const e=document.elementFromPoint(x,y);
              return this.isConnected && this.ownerDocument===document && (e===this || this.contains(e));}
            """, [x, y])
            if hit is not True:
                continue
            self.resolve(reference, generation)
            for kind in ("mousePressed", "mouseReleased"):
                self.cdp.send("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1,
                })
            return
        raise Invalid("reference is not an actionable click target")

    def close(self):
        if self.closed:
            return
        self.closed = True
        for node in self.nodes.values():
            try:
                self.cdp.send("Runtime.releaseObject", {"objectId": node})
            except Exception:
                pass
        self.nodes.clear()


class InspectionBrowser:
    """Owner-scoped opt-in process reuse; run_browser always makes a new context."""

    def __init__(self, factory, *, lifetime=120, calls=8, clock=time.monotonic):
        self.factory, self.clock = factory, clock
        self.lifetime, self.remaining = lifetime, calls
        self.browser = None

    def __enter__(self):
        self.manager = self.factory()
        self.playwright = self.manager.__enter__()
        try:
            self.browser = self.playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception:
            self.manager.__exit__(*__import__('sys').exc_info())
            raise
        self.deadline = self.clock() + self.lifetime
        return self

    def inspect(self, bundle, *, document_driver=None):
        from preview_inspection_capsule import run_browser
        if self.browser is None or self.remaining <= 0 or self.clock() >= self.deadline:
            raise Invalid("inspection browser lease expired")
        self.remaining -= 1
        previous = set(self.browser.contexts)
        try:
            return run_browser(bundle, shared_browser=self.browser, document_driver=document_driver)
        finally:
            # Also covers page startup/navigation failures before the document
            # driver is established. Never close a pre-existing context.
            for context in self.browser.contexts:
                if context not in previous:
                    context.close()

    def __exit__(self, *args):
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            self.browser = None
            self.manager.__exit__(*args)
