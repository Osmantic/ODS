"""Boundaries and opt-in real browser regressions (ODS_PREVIEW_BROWSER_TESTS=1)."""

import base64
import collections
import copy
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/host")
)
import preview_inspection_protocol as protocol
import preview_inspection as broker
import preview_inspection_capsule as capsule


def step(action, selector=None, name=None):
    return {
        "action": action,
        "locator": {"selector": selector}
        if selector is not None
        else {"role": "button", "name": name, "exact": True},
    }


def role_step(action, role, name):
    return {"action": action, "locator": {"role": role, "name": name, "exact": True}}


# Round 060 (strixy, Qwen3.6-35B-A3B): the owner's hidden sold-out card. The
# model asserted it hidden by exact role/name, clicked, then asserted it
# visible; every assert-hidden returned count 0 and it rewrote the site 4 times.
FLEET_EVENTS_HTML = (
    "<!doctype html><title>Riverside Events</title><h1>Riverside Events</h1>"
    '<section class="cards"><article class="card"><h2>Dawn Jazz</h2></article>'
    '<article class="card"><h2>River Lantern Walk</h2></article>'
    '<article class="card" id="midnight-card" hidden><h2>Midnight Sold-Out Concert</h2>'
    '<span class="tag">Sold out</span></article></section>'
    '<button id="toggle" onclick="const c=document.getElementById(\'midnight-card\');'
    "c.hidden=!c.hidden;this.textContent=c.hidden?'Show sold out':'Hide sold out'\">Show sold out</button>"
)
MIDNIGHT = ("heading", "Midnight Sold-Out Concert")
FLEET_PLAN = [
    role_step("assert-visible", "heading", "Dawn Jazz"),
    role_step("assert-visible", "heading", "River Lantern Walk"),
    role_step("assert-hidden", *MIDNIGHT),
    role_step("click", "button", "Show sold out"),
    role_step("assert-visible", *MIDNIGHT),
]


def bundle(html, steps=None, files=None):
    # files: {path: bytes} for a multi-file site; html alone is index.html.
    files = sorted((files or {"index.html": html.encode()}).items())
    digest = hashlib.sha256()
    for name, data in files:
        digest.update(len(name.encode()).to_bytes(4, "big") + name.encode())
        digest.update(len(data).to_bytes(8, "big") + data)
    digest = digest.hexdigest()
    return {
        "schemaVersion": 1,
        "request": {
            "schemaVersion": 1,
            "action": "inspect",
            "siteId": "site-" + digest[:24],
            "sha256": digest,
            "viewport": {"width": 375, "height": 812},
            "steps": steps or [step("assert-visible", "#item")],
        },
        "files": [{"path": name, "base64": base64.b64encode(data).decode()}
                  for name, data in files],
    }


# Fleet round 069 (tower1, Qwen3.5-27B): "change the accent to amber". The
# model set --accent-color to #ffc107, which only styles :focus outlines; the
# page as rendered stayed green and white, and the fleet found no amber paint.
TOWER1_R069 = Path(__file__).resolve().parent / "fixtures/preview-palette/tower1-r069"


def tower1_files(**replacements):
    files = {path.name: path.read_bytes() for path in sorted(TOWER1_R069.iterdir())}
    css = files["styles.css"].decode()
    for old, new in replacements.items():
        assert old in css, old
        css = css.replace(old, new)
    files["styles.css"] = css.encode()
    return files


# The repair the owner asked for: the visible primary colors become amber.
AMBER_REPAIR = {"--primary-color: #2d5a3d;": "--primary-color: #ffa000;",
                "--secondary-color: #4a7c59;": "--secondary-color: #ffc107;"}
DARK_PAGE_HTML = (
    "<!doctype html><title>Night Garden</title><style>body{margin:0;background:#121212;color:#e0e0e0;"
    "font:16px sans-serif}header{padding:48px;background:#1e1e1e}main{padding:32px}"
    ".card{background:#2c2c2c;padding:24px;margin:16px 0;border-radius:8px}"
    "button{background:#bb86fc;color:#121212;border:0;padding:24px 64px;font-size:20px}</style>"
    "<header><h1>Night Garden</h1></header><main><div class=card>Dawn Jazz</div>"
    "<div class=card>River Lantern Walk</div><button>Show sold out</button></main>"
)


class ObservationTests(unittest.TestCase):
    def sample(self, values, evaluation_cost=0):
        clock = [0.0]
        waits = []
        samples = iter(values)

        def once():
            clock[0] += evaluation_cost
            return next(samples)

        def wait(milliseconds):
            waits.append(milliseconds)
            clock[0] += milliseconds / 1000

        with patch.object(capsule.time, "monotonic", side_effect=lambda: clock[0]):
            result = capsule.observe_until_stable(once, wait)
        return result, waits, clock[0]

    def test_stable_fast_path(self):
        result, waits, elapsed = self.sample([{"opacity": "1"}] * 2)
        self.assertEqual(result, ({"opacity": "1"}, True))
        self.assertEqual(waits, [100])
        self.assertEqual(elapsed, .1)

    def test_disagreement_requires_new_identical_pair(self):
        values = [{"visible": True, "opacity": str(v)} for v in (.1, .4, 1, 1)]
        result, waits, _ = self.sample(values)
        self.assertEqual(result, (values[-1], True))
        self.assertEqual(waits, [100] * 3)

    def test_perpetual_change_stops_within_budget(self):
        result, waits, elapsed = self.sample([{"opacity": str(v)} for v in range(30)])
        self.assertFalse(result[1])
        self.assertLessEqual(elapsed, 1.5)
        self.assertLessEqual(len(waits), 15)

    def test_late_matching_result_does_not_pass(self):
        result, _, elapsed = self.sample([{"visible": True}] * 2, evaluation_cost=.8)
        self.assertGreater(elapsed, 1.5)
        self.assertFalse(result[1])


class ProtocolTests(unittest.TestCase):
    def test_production_frame_contract(self):
        import workspace_preview as publisher

        self.assertEqual(protocol.CSP, publisher.CSP)
        dashboard = (
            Path(__file__).resolve().parents[1]
            / "extensions/services/dashboard/src/pages/Pixel.jsx"
        ).read_text()
        self.assertIn("sandbox: '" + protocol.SANDBOX + "'", dashboard)

    def test_binding(self):
        good = bundle("hi")
        protocol.validate_bundle(good)
        for mutation in ("hash", "bytes", "path", "duplicate", "unknown"):
            bad = copy.deepcopy(good)
            if mutation == "hash":
                bad["request"].update(sha256="0" * 64, siteId="site-" + "0" * 24)
            elif mutation == "bytes":
                bad["files"][0]["base64"] = "eA=="
            elif mutation == "path":
                bad["files"][0]["path"] = "../index.html"
            elif mutation == "duplicate":
                bad["files"] *= 2
            else:
                bad["files"][0]["url"] = "http://localhost"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                protocol.validate_bundle(bad)

    def test_no_script_url_unbounded_locator(self):
        for locator in (
            {"selector": "xpath=//*"},
            {"selector": "a >> iframe"},
            {"selector": "x" * 257},
            {"role": "button", "name": "ok", "exact": False},
            {"role": "arbitrary", "name": "ok", "exact": True},
            {"url": "http://localhost"},
            {"eval": "process.exit()"},
        ):
            request = bundle("hi")["request"]
            request["steps"] = [{"action": "click", "locator": locator}]
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                protocol.validate_request(request)
        request = bundle("hi")["request"]
        request["steps"] *= 13
        with self.assertRaises(ValueError):
            protocol.validate_request(request)
        with self.assertRaises(ValueError):
            protocol.strict_json('{"a":1,"a":2}')

    def test_isolation(self):
        config = {
            "docker": "/usr/bin/docker",
            "imageId": "sha256:" + "a" * 64,
            "transport": "local",
        }
        argv = broker.capsule_argv(config, "ods-preview-inspection-owned")
        for flag in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pull=never",
            "--memory=1g",
            "--pids-limit=128",
        ):
            self.assertIn(flag, argv)
        for flag in (
            "-v",
            "--volume",
            "--mount",
            "--env",
            "--privileged",
            "--network=host",
        ):
            self.assertNotIn(flag, argv)
        self.assertEqual(
            argv[:3], ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
        )

    def test_timeout_output_and_cancel(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ValueError, "timeout"):
            broker.bounded_process(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                b"",
                timeout=0.1,
                limit=100,
            )
        self.assertLess(time.monotonic() - start, 3)
        with self.assertRaisesRegex(ValueError, "output_limit"):
            broker.bounded_process(
                [sys.executable, "-c", 'print("x"*10000)'], b"", timeout=2, limit=100
            )
        import threading

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            broker.bounded_process(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                b"",
                timeout=2,
                limit=100,
                cancelled=cancelled,
            )

    def test_container_cleanup(self):
        request = bundle("hi")["request"]
        config = {
            "docker": "/usr/bin/docker",
            "imageId": "sha256:" + "a" * 64,
            "ownerUid": os.getuid(),
            "transport": "local",
            "snapshotRoot": "/owned",
        }
        calls = []

        def process(argv, *args, **kwargs):
            calls.append(argv)
            if "run" in argv:
                raise protocol.Invalid("cancelled")
            return b""

        with (
            patch.object(broker, "snapshot_bundle", return_value=bundle("hi")),
            patch.object(broker, "bounded_process", side_effect=process),
        ):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                broker.inspect_request(request, config)
        self.assertEqual(calls[1][3:5], ["rm", "-f"])
        self.assertEqual(calls[1][5], calls[0][calls[0].index("--name") + 1])

    def test_peer(self):
        a, b = socket.socketpair()
        try:
            with (
                patch("unix_peer.peer_ids", return_value=(123, 123)),
                patch.object(broker, "inspect_request") as run,
            ):
                broker.handle(a, {"ownerUid": 456})
                self.assertFalse(run.called)
                self.assertEqual(protocol.strict_json(b.recv(4096))["status"], "failed")
        finally:
            a.close()
            b.close()

    def test_snapshot_mode_symlink(self):
        import workspace_preview as publisher

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            site = workspace / "site"
            site.mkdir(mode=0o700)
            (site / "index.html").write_text('<p id="item">hi</p>')
            (site / "index.html").chmod(0o600)
            previews = root / "previews"
            previews.mkdir(mode=0o700)
            receipt = publisher.publish_snapshot(
                workspace, previews, "site", os.getuid()
            )
            request = bundle("hi")["request"]
            request.update(siteId=receipt["siteId"], sha256=receipt["sha256"])
            protocol.validate_bundle(broker.snapshot_bundle(previews, request))
            target = previews / receipt["siteId"] / "index.html"
            target.chmod(0o600)
            with self.assertRaises(ValueError):
                broker.snapshot_bundle(previews, request)
            target.parent.chmod(0o700)
            target.unlink()
            target.symlink_to(site / "index.html")
            with self.assertRaises(publisher.PreviewError):
                broker.snapshot_bundle(previews, request)


STORAGE_ERROR = (
    "Failed to read the 'sessionStorage' property from 'Window': The document "
    "is sandboxed and lacks the 'allow-same-origin' flag."
)


class PageError:
    def __init__(self, name, message, stack=""):
        self.name, self.message, self.stack = name, message, stack


# Observed with Playwright's service_workers="block" in an opaque-origin
# preview frame: its URL-less init script throws before any page script runs.
AUTOMATION_ERROR = PageError(
    "SecurityError",
    "Failed to read the 'serviceWorker' property from 'Navigator': Service worker "
    "is disabled because the context is sandboxed and lacks the 'allow-same-origin' flag.",
    "SecurityError: Failed to read the 'serviceWorker' property from 'Navigator'.\n"
    "    at <anonymous>:3:15\n    at <anonymous>:5:7",
)


class ScriptedBrowser:
    """Minimal Playwright double: the scripted page throws while loading and
    while handling a click, as an author's page would. The real browser path
    is covered by the opt-in BrowserTests and DockerCapsuleTests below."""

    def __init__(self, load_errors=(), click_errors=(), palette=None):
        self.load_errors, self.click_errors = list(load_errors), list(click_errors)
        self.handlers, self.calls, self.revealed = {}, [], False
        # Without a screenshot the capture fails and the palette is omitted.
        self.palette = palette or PaletteDouble(None)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, **_):
        return self

    def new_context(self, **kwargs):
        # The rendered palette uses its own context (see PaletteDouble).
        if "device_scale_factor" in kwargs:
            self.calls.append("new_context:palette")
            self.palette.kwargs, self.palette.site = kwargs, self.site
            return self.palette
        return self

    def route(self, *_):
        pass

    def on(self, event, handler):
        self.calls.append("on:" + event)
        self.handlers.setdefault(event, []).append(handler)

    def new_page(self):
        return self

    def set_default_timeout(self, _):
        pass

    def emit(self, errors):
        for error in errors:
            for handler in self.handlers.get("pageerror", []):
                handler(error)

    def goto(self, url, **_):
        self.calls.append("goto")
        self.url = url.replace("/__ods_inspection__.html", "/" + self.site + "/")
        self.emit(self.load_errors)

    def frame(self, name):
        return self if name == "inspection" else None

    def locator(self, _):
        return self

    def click(self, **_):
        self.revealed = True
        self.emit(self.click_errors)

    def wait_for_timeout(self, _):
        pass

    def new_cdp_session(self, _):
        return self

    def send(self, method, params=None):
        params = params or {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"childFrames": [{"frame": {"id": "F", "name": "inspection"}}]}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 7}
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "document"}}
        if method == "Runtime.releaseObject":
            return {}
        function = params.get("functionDeclaration")
        if function == capsule.DIAGNOSTIC:
            return {"result": {"value": {"renderedHiddenAttributeCount": 0, "hiddenUntilFoundCount": 0}}}
        if function == capsule.SELECTOR_COUNT:
            return {"result": {"value": {"count": 1}}}
        if function == capsule.OBSERVE_ELEMENT:
            visible = params["objectId"] != "#item" or self.revealed
            return {"result": {"value": {
                "count": 1, "visible": visible, "display": "block" if visible else "none",
                "visibility": "visible", "opacity": "1", "hidden": not visible,
                "hiddenUntilFound": False, "rectCount": int(visible)}}}
        return {"result": {"objectId": params["arguments"][0]["value"]}}

    def close(self):
        self.calls.append("close")


class Route:
    def __init__(self, url, navigation=False, method="GET"):
        self.request, self.url, self.method, self.navigation = self, url, method, navigation
        self.outcome = None

    def is_navigation_request(self):
        return self.navigation

    def continue_(self):
        self.outcome = "continued"

    def abort(self):
        self.outcome = "aborted"


class PaletteDouble:
    """The palette context and page: a separate load that renders `image`.
    `during_load` routes a request through the installed guard as the page
    loads, and `error` makes the screenshot fail."""

    def __init__(self, image, during_load=None, error=None, frame_url=None):
        self.image, self.during_load, self.error, self.frame_url = image, during_load, error, frame_url
        self.calls, self.handlers, self.routes, self.kwargs, self.shot = [], {}, [], None, None

    def new_page(self):
        return self

    def set_default_timeout(self, _):
        pass

    def route(self, _pattern, handler):
        self.guard = handler

    def on(self, event, handler):
        self.calls.append("on:" + event)
        self.handlers.setdefault(event, []).append(handler)

    def goto(self, url, **_):
        self.calls.append("goto")
        self.origin = url.split("/__ods_inspection__.html")[0]
        self.url = self.frame_url or self.origin + "/" + self.site + "/"
        for relative, navigation in [("/" + self.site + "/styles.css", False),
                                     *(self.during_load or [])]:
            route = Route(self.origin + relative if relative.startswith("/") else relative, navigation)
            self.guard(route)
            self.routes.append(route)

    def frame(self, name):
        return self if name == "inspection" else None

    def wait_for_timeout(self, _):
        pass

    def screenshot(self, **kwargs):
        self.shot = kwargs
        if self.error:
            raise self.error
        return self.image

    def close(self):
        self.calls.append("close")


class PageErrorTests(unittest.TestCase):
    def run_scripted(self, browser):
        data = bundle("<p id=item hidden></p><button id=show>Show</button>", [
            step("assert-hidden", "#item"), step("click", "#show"), step("assert-visible", "#item")])
        browser.site = data["request"]["siteId"]
        result = capsule.run_browser(data, playwright_factory=browser)
        self.assertLessEqual(len(protocol.canonical(result)), protocol.MAX_RESULT)
        return data["request"], result

    def test_uncaught_errors_are_bounded_evidence_without_changing_status(self):
        long_message = "\u202eline one\nline two\u200b\u2028" + "x" * 400 + "\ud800"
        browser = ScriptedBrowser(
            load_errors=[AUTOMATION_ERROR, *[PageError("SecurityError", STORAGE_ERROR)] * 3],
            click_errors=[PageError("TypeError", long_message), PageError("RangeError", "too deep"),
                          PageError("ReferenceError", "dropped after three distinct")])
        request, result = self.run_scripted(browser)
        # The listener exists before navigation, so startup exceptions count.
        self.assertLess(browser.calls.index("on:pageerror"), browser.calls.index("goto"))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual([item["status"] for item in result["steps"]], ["passed"] * 3)
        errors = result["pageErrors"]
        self.assertEqual(errors["count"], 6)
        self.assertEqual(errors["messages"][0], "SecurityError: " + STORAGE_ERROR)
        self.assertEqual(errors["messages"][2], "RangeError: too deep")
        typed = errors["messages"][1]
        self.assertEqual(len(typed), capsule.MAX_PAGE_ERROR_CHARS)
        self.assertTrue(typed.startswith("TypeError: line one line two x"), typed)
        self.assertTrue(typed.endswith("\u2026"))
        for message in errors["messages"]:
            self.assertTrue(protocol.printable(message, 200, 800), message)
        self.assertEqual(result["planSha256"], protocol.plan_hash(request))
        self.assertEqual(set(result) - {"pageErrors"}, set(self.run_scripted(ScriptedBrowser())[1]))

    def test_no_uncaught_error_keeps_legacy_receipt_shape(self):
        _, result = self.run_scripted(ScriptedBrowser())
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(set(result), {"schemaVersion", "kind", "status", "siteId", "sha256",
            "planSha256", "viewport", "steps", "diagnostics", "blockedRequests", "scope"})

    def test_page_error_text_is_inert_and_bounded(self):
        cases = {
            "": "(no message)",
            "\x00\x1b[31m\u2029\ufeff": "[31m",
            "a\r\n\tb\u2028c": "a b c",
            "\ue000private\U000e0001tag": "private tag",
            "\ud83d lone surrogate": "lone surrogate",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=ascii(raw)):
                self.assertEqual(capsule.page_error_text(raw), expected)
                protocol.canonical({"message": capsule.page_error_text(raw)})
        self.assertEqual(capsule.page_error_text("y" * 100000), "y" * 199 + "\u2026")

    def test_record_is_saturating_distinct_and_never_raises(self):
        errors = capsule.PageErrors()
        self.assertEqual(errors.receipt(), {})

        class Hostile:
            @property
            def message(self):
                raise RuntimeError("author getter")

        errors.record(Hostile())
        errors.record(PageError("", ""))
        errors.record(PageError(None, "plain"))
        errors.record(PageError("Error", "Error: already named"))
        for _ in range(capsule.MAX_PAGE_ERRORS + 50):
            errors.record(PageError("Error", "flood"))
        self.assertEqual(errors.receipt(), {"pageErrors": {
            "count": capsule.MAX_PAGE_ERRORS,
            "messages": ["(no message)", "plain", "Error: already named"]}})

    def test_automation_injected_exceptions_are_not_page_errors(self):
        origin = "http://127.0.0.1:41234/site-" + "a" * 24
        errors = capsule.PageErrors()
        for error in (
            AUTOMATION_ERROR,
            PageError("Error", "hidden",
                      "Error: hidden\n    at Object.run (<anonymous>:1:2)\n    at async <anonymous>"),
        ):
            errors.record(error)
        self.assertEqual(errors.receipt(), {})
        # Stack shapes observed from page code: top-level (no frames), named
        # functions, inline handlers, and an external script's anonymous body.
        for error in (
            PageError("SecurityError", STORAGE_ERROR),
            PageError("SecurityError", "Failed to read the 'localStorage' property",
                      f"SecurityError: x\n    at boot ({origin}/:1:62)\n    at {origin}/:1:70"),
            PageError("TypeError", "Cannot read properties of undefined (reading 'x')",
                      f"TypeError: x\n    at HTMLButtonElement.onclick ({origin}/:1:48)"),
            PageError("Uncaught SecurityError", "external",
                      f"Uncaught SecurityError: external\n    at <anonymous> ({origin}/app.js:1:0)"),
            PageError("TypeError", "mixed",
                      f"TypeError: mixed\n    at <anonymous>:1:1\n    at {origin}/:1:9"),
        ):
            errors.record(error)
        self.assertEqual(errors.receipt()["pageErrors"]["count"], 5)

    def test_wrapper_contributes_no_script(self):
        document = capsule.wrapper_document("/site-" + "a" * 24 + "/").decode()
        self.assertNotIn("<script", document.lower())
        self.assertNotRegex(document, r"\son[a-z]+=")
        self.assertIn('sandbox="' + protocol.SANDBOX + '"', document)
        self.assertNotIn("allow-same-origin", document)

    def test_broker_relays_page_errors_unchanged(self):
        data = bundle("hi")
        request = data["request"]
        receipt = {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed",
                   "siteId": request["siteId"], "sha256": request["sha256"],
                   "planSha256": protocol.plan_hash(request), "viewport": request["viewport"],
                   "steps": [], "diagnostics": {}, "blockedRequests": [],
                   "pageErrors": {"count": 1, "messages": ["SecurityError: " + STORAGE_ERROR]},
                   "scope": protocol.SCOPE}
        config = {"docker": "/usr/bin/docker", "imageId": "sha256:" + "a" * 64,
                  "ownerUid": os.getuid(), "transport": "local", "snapshotRoot": "/owned"}

        def process(argv, *_args, **_kwargs):
            return protocol.canonical(receipt) if "run" in argv else b""

        with (
            patch.object(broker, "snapshot_bundle", return_value=data),
            patch.object(broker, "bounded_process", side_effect=process),
        ):
            self.assertEqual(broker.inspect_request(request, config), receipt)


class SemanticBrowser(ScriptedBrowser):
    """The fleet page through the capsule's CDP calls. Chromium's accessibility
    tree (queryAXTree) exposes only rendered, named elements; the isolated-world
    hidden-inclusive matcher is modelled by the element table below."""

    def __init__(self, elements):
        super().__init__()
        # element id -> (role, name, visible before click, visible after click)
        self.elements = elements
        self.matcher_calls, self.live = [], {}

    def visible(self, element):
        return self.elements[element][3 if self.revealed else 2]

    def remote(self, value):
        object_id = "obj-%d" % (len(self.live) + len(self.released))
        self.live[object_id] = value
        return object_id

    @property
    def released(self):
        return self.__dict__.setdefault("_released", [])

    def send(self, method, params=None):
        params = params or {}
        function = params.get("functionDeclaration")
        if method == "Accessibility.queryAXTree":
            return {"nodes": [
                {"role": {"value": role}, "name": {"value": name}, "backendDOMNodeId": element}
                for element, (role, name, *_) in self.elements.items()
                if (role, name) == (params["role"], params["accessibleName"]) and self.visible(element)
            ]}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": self.remote(params["backendNodeId"])}}
        if method == "Runtime.releaseObject":
            self.released.append(params["objectId"])
            self.live.pop(params["objectId"])
            return {}
        if function == capsule.ROLE_NAME_INCLUDING_HIDDEN:
            role, name, *rendered = [a.get("value", a.get("objectId")) for a in params["arguments"]]
            self.matcher_calls.append((role, name))
            union = [self.live[r] for r in rendered]
            union += [e for e, (r, n, *_) in self.elements.items() if (r, n) == (role, name) and e not in union]
            return {"result": {"objectId": self.remote(union)}}
        if function == "function(){return this.length}":
            return {"result": {"value": len(self.live[params["objectId"]])}}
        if function == "function(){return this[0]}":
            return {"result": {"objectId": self.remote(self.live[params["objectId"]][0])}}
        if function == capsule.OBSERVE_ELEMENT:
            visible = self.visible(self.live[params["objectId"]])
            return {"result": {"value": {
                "count": 1, "visible": visible, "display": "block" if visible else "none",
                "visibility": "visible", "opacity": "1", "hidden": not visible,
                "hiddenUntilFound": False, "rectCount": int(visible)}}}
        return super().send(method, params)

    def get_by_role(self, role, name, exact):
        assert exact is True
        return self


class HiddenRoleLocatorTests(unittest.TestCase):
    FLEET = {
        "dawn": ("heading", "Dawn Jazz", True, True),
        "river": ("heading", "River Lantern Walk", True, True),
        "midnight": ("heading", "Midnight Sold-Out Concert", False, True),
        "toggle": ("button", "Show sold out", True, False),
    }

    def run_plan(self, elements, steps):
        browser = SemanticBrowser(elements)
        data = bundle(FLEET_EVENTS_HTML, steps)
        browser.site = data["request"]["siteId"]
        return browser, capsule.run_browser(data, playwright_factory=browser)

    def test_fleet_plan_passes_with_hidden_inclusive_assert_hidden(self):
        browser, result = self.run_plan(self.FLEET, FLEET_PLAN)
        self.assertEqual(result["status"], "passed", result)
        hidden = result["steps"][2]
        self.assertEqual(hidden["before"]["count"], 1)
        self.assertFalse(hidden["before"]["visible"])
        self.assertTrue(result["steps"][4]["before"]["visible"])
        # Only the hidden assertion used hidden-inclusive matching; the click
        # and assert-visible steps kept Chromium's rendered-only resolution.
        self.assertEqual(set(browser.matcher_calls), {MIDNIGHT})
        self.assertEqual(browser.live, {}, "every remote object is released")

    def test_rendered_only_resolution_is_unchanged_for_assert_visible(self):
        _, result = self.run_plan(self.FLEET, [role_step("assert-visible", *MIDNIGHT)])
        self.assertEqual(result["steps"][0]["before"], {"count": 0})
        self.assertEqual(result["steps"][0]["errorCode"], "no_match")

    def test_hide_transition_uses_the_same_locator(self):
        elements = {**self.FLEET, "midnight": ("heading", "Midnight Sold-Out Concert", True, False)}
        _, result = self.run_plan(elements, [role_step("assert-visible", *MIDNIGHT),
                                             role_step("click", "button", "Show sold out"),
                                             role_step("assert-hidden", *MIDNIGHT)])
        self.assertEqual(result["status"], "passed", result)

    def test_visible_target_of_assert_hidden_is_a_visibility_mismatch(self):
        _, result = self.run_plan(self.FLEET, [role_step("assert-hidden", "heading", "Dawn Jazz")])
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch")
        self.assertEqual(result["steps"][0]["before"]["count"], 1)

    def test_uniqueness_counts_rendered_and_hidden_matches(self):
        elements = {**self.FLEET, "copy": ("heading", "Midnight Sold-Out Concert", True, True)}
        browser, result = self.run_plan(elements, [role_step("assert-hidden", *MIDNIGHT)])
        self.assertEqual(result["steps"][0]["before"], {"count": 2})
        self.assertEqual(result["steps"][0]["errorCode"], "selector_not_unique")
        self.assertEqual(browser.live, {})

    def test_no_match_is_reported_as_no_match(self):
        for plan in ([role_step("assert-hidden", "heading", "Midnight sold-out concert")],
                     [step("assert-hidden", "#missing")]):
            with self.subTest(plan=plan):
                browser = SemanticBrowser(self.FLEET)
                if "selector" in plan[0]["locator"]:
                    browser = ScriptedBrowser()
                    browser.send = lambda method, params=None, send=browser.send: (
                        {"result": {"value": {"count": 0}}}
                        if (params or {}).get("functionDeclaration") == capsule.SELECTOR_COUNT
                        else send(method, params))
                data = bundle(FLEET_EVENTS_HTML, plan)
                browser.site = data["request"]["siteId"]
                result = capsule.run_browser(data, playwright_factory=browser)
                self.assertEqual(result["steps"][0]["before"], {"count": 0})
                self.assertEqual(result["steps"][0]["errorCode"], "no_match")
                self.assertEqual(result["status"], "failed")

    def test_matcher_source_is_bounded_and_read_only(self):
        source = capsule.ROLE_NAME_INCLUDING_HIDDEN
        for forbidden in ("setAttribute", "removeAttribute", "innerHTML", "textContent =",
                          ".style.", "click(", "dispatchEvent", "focus(", "fetch(", "eval(", "Function("):
            self.assertNotIn(forbidden, source)


# Fleet round 100 (tower2, Qwen3-Coder-Next): the owner asked for a button
# named exactly "Show sold out"; script.js replaced its name on load. The
# plugin replay (control-names-tower2-round100.json) carries the load-time
# names this capsule reports for those bytes, and for three small pages.
CONTROL_REPLAY = json.loads(
    (Path(__file__).resolve().parents[1]
     / "extensions/services/pixel-agent/tests/control-names-tower2-round100.json").read_text(encoding="utf-8")
)
TOWER2_R100 = Path(__file__).resolve().parent / "fixtures/preview-controls/tower2-r100"


def tower2_r100_files(repaired=False, variant=None):
    files = {path.name: path.read_bytes() for path in sorted(TOWER2_R100.iterdir())}
    if repaired or variant:
        line = CONTROL_REPLAY["repair"]["remove"].encode()
        assert files["script.js"].count(line) == 1
        files["script.js"] = files["script.js"].replace(line, b"")
    if variant:
        # The repaired page with only the requested button's markup changed:
        # correct, accessible names that carry hidden decorations.
        button = CONTROL_REPLAY["variants"]["button"].encode()
        assert files["index.html"].count(button) == 1
        files["index.html"] = files["index.html"].replace(
            button, CONTROL_REPLAY["variants"]["markup"][variant].encode())
    return files


TOWER2_R100_CSS_PLAN = [step("assert-hidden", "#midnight-concert"), step("click", "#show-sold-out-btn"),
                        step("assert-visible", "#midnight-concert")]


def fixture_server(files):
    """A Playwright route handler serving a fixture site's files by name."""
    types = {"html": "text/html", "js": "application/javascript", "css": "text/css"}

    def serve(route):
        path = route.request.url.rsplit("/", 1)[1] or "index.html"
        route.fulfill(body=files[path], content_type=types[path.rsplit(".", 1)[1]])

    return serve


class NamedBrowser(ScriptedBrowser):
    """ScriptedBrowser whose isolated world answers CONTROL_NAMES."""

    def __init__(self, names):
        super().__init__()
        self.names = names

    def send(self, method, params=None):
        function = (params or {}).get("functionDeclaration")
        if function == capsule.CONTROL_NAMES:
            self.calls.append("controls")
            self.limit = params["arguments"][0]["value"]
            return {"result": {"value": self.names}}
        if function == capsule.OBSERVE_ELEMENT:
            self.calls.append("observe")
        return super().send(method, params)

    def click(self, **kwargs):
        self.calls.append("click")
        super().click(**kwargs)


def raw_control(name="Show sold out", **extra):
    return {"role": "button", "name": name, "text": name, "source": "content", "visible": True, **extra}


class ControlNamesTests(unittest.TestCase):
    def run_named(self, names):
        browser = NamedBrowser(names)
        data = bundle("<p id=item hidden></p><button id=show>Show</button>", [
            step("assert-hidden", "#item"), step("click", "#show"), step("assert-visible", "#item")])
        browser.site = data["request"]["siteId"]
        result = capsule.run_browser(data, playwright_factory=browser)
        self.assertLessEqual(len(protocol.canonical(result)), protocol.MAX_RESULT)
        return browser, data["request"], result

    def test_load_time_names_are_captured_before_any_step_as_separate_evidence(self):
        replaced = raw_control("Show the sold out midnight concert card", text="Show sold out", source="aria-label")
        browser, request, result = self.run_named({"count": 1, "items": [replaced]})
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["controls"], {"count": 1, "items": [{
            "role": "button", "name": "Show the sold out midnight concert card", "visible": True,
            "source": "aria-label", "text": "Show sold out"}]})
        self.assertEqual(browser.limit, capsule.MAX_CONTROLS)
        self.assertLess(browser.calls.index("controls"), browser.calls.index("observe"))
        self.assertLess(browser.calls.index("controls"), browser.calls.index("click"))
        self.assertEqual(result["planSha256"], protocol.plan_hash(request))
        without = self.run_named(None)[2]
        self.assertNotIn("controls", without, "a failed capture is omitted, as by older capsules")
        self.assertEqual({k: v for k, v in result.items() if k != "controls"}, without)

    def test_names_are_inert_bounded_and_text_only_when_it_differs(self):
        long_name = "‮Show​ sold\nout " + "x" * 400
        controls = capsule.control_names({"count": 5, "items": [
            raw_control(),
            raw_control("", text="", source="other", visible=False),
            raw_control(long_name, text="\x00Show   sold out "),
            raw_control("Docs", role="link", text="Docs"),
        ]})
        self.assertEqual(controls["count"], 5, "the page total, not the listed count")
        first, unnamed, bounded, link = controls["items"]
        self.assertEqual(first, {"role": "button", "name": "Show sold out", "visible": True, "source": "content"})
        self.assertEqual(unnamed, {"role": "button", "name": "", "visible": False, "source": "other"})
        self.assertEqual(len(bounded["name"]), capsule.MAX_CONTROL_CHARS)
        self.assertTrue(bounded["name"].startswith("Show sold out xxx"), bounded)
        self.assertTrue(bounded["name"].endswith("…"))
        self.assertEqual(bounded["text"], "Show sold out")
        self.assertEqual(link["role"], "link")
        for item in controls["items"]:
            for key in ("name", "text"):
                if item.get(key):
                    self.assertTrue(protocol.printable(item[key], capsule.MAX_CONTROL_CHARS, 480), item)
        saturated = capsule.control_names({"count": 10**6, "items": [raw_control()] * 100})
        self.assertEqual(saturated["count"], capsule.MAX_CONTROL_COUNT)
        self.assertEqual(len(saturated["items"]), capsule.MAX_CONTROLS)
        # The encoded size bound cuts the list; every item stays whole.
        wide = capsule.control_names({"count": 48, "items": [
            raw_control("一" * 200, text="二" * 200)] * 48})
        self.assertLess(len(wide["items"]), 48)
        self.assertLessEqual(sum(len(protocol.canonical(item)) + 1 for item in wide["items"]),
                             capsule.MAX_CONTROLS_BYTES)
        self.assertEqual(wide["count"], 48)
        for bad in (None, [], {"count": "1", "items": []}, {"count": 1}, {"count": 1, "items": [raw_control(role="tab")]},
                    {"count": 1, "items": [raw_control(visible="yes")]}, {"count": 1, "items": [raw_control(source="title")]},
                    {"count": 1, "items": ["button"]}):
            with self.subTest(bad=bad):
                with self.assertRaises(protocol.Invalid):
                    capsule.control_names(bad)

    def test_an_invalid_capture_is_omitted_not_a_failed_receipt(self):
        _, _, result = self.run_named({"count": 1, "items": [raw_control(role="menuitem")]})
        self.assertEqual(result["status"], "passed")
        self.assertNotIn("controls", result)

    def test_names_never_push_a_receipt_past_its_limit(self):
        _, _, kept = self.run_named({"count": 1, "items": [raw_control()]})
        self.assertIn("controls", kept)
        without = {k: v for k, v in kept.items() if k != "controls"}
        limit = len(protocol.canonical(without)) + 16
        self.assertGreater(len(protocol.canonical(kept)), limit)
        with patch.object(capsule, "MAX_RESULT", limit):
            _, _, result = self.run_named({"count": 1, "items": [raw_control()]})
        self.assertEqual(result, without, "dropped whole; nothing else changes")

    def test_names_share_the_matcher_rules_and_are_read_only(self):
        self.assertIn(capsule.ACCESSIBLE_NAME_RULES, capsule.CONTROL_NAMES)
        self.assertIn(capsule.ACCESSIBLE_NAME_RULES, capsule.ROLE_NAME_INCLUDING_HIDDEN)
        self.assertTrue(capsule.CONTROL_NAMES.startswith("function(limit) {\n"))
        self.assertTrue(capsule.ROLE_NAME_INCLUDING_HIDDEN.startswith("function(role, name, ...rendered) {\n"))
        for forbidden in ("setAttribute", "removeAttribute", "innerHTML", "textContent =",
                          ".style.", "click(", "dispatchEvent", "focus(", "fetch(", "eval(", "Function("):
            self.assertNotIn(forbidden, capsule.CONTROL_NAMES)

    def test_broker_relays_controls_unchanged(self):
        data = bundle("hi")
        request = data["request"]
        receipt = {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed",
                   "siteId": request["siteId"], "sha256": request["sha256"],
                   "planSha256": protocol.plan_hash(request), "viewport": request["viewport"],
                   "steps": [], "diagnostics": {}, "blockedRequests": [],
                   "controls": CONTROL_REPLAY["controls"]["tower2"], "scope": protocol.SCOPE}
        config = {"docker": "/usr/bin/docker", "imageId": "sha256:" + "a" * 64,
                  "ownerUid": os.getuid(), "transport": "local", "snapshotRoot": "/owned"}

        def process(argv, *_args, **_kwargs):
            return protocol.canonical(receipt) if "run" in argv else b""

        with (
            patch.object(broker, "snapshot_bundle", return_value=data),
            patch.object(broker, "bounded_process", side_effect=process),
        ):
            self.assertEqual(broker.inspect_request(request, config), receipt)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode_png(width, height, pixels, alpha=False, filters=(0,), depth=8, color_type=None, interlace=0):
    """Reference encoder: row y uses filter type filters[y % len(filters)]."""
    channels = 4 if alpha else 3
    raw, previous = bytearray(), bytes(width * channels)
    for y in range(height):
        row = bytearray()
        for r, g, b in pixels[y * width:(y + 1) * width]:
            row += bytes((r, g, b, 255)[:channels])
        kind = filters[y % len(filters)]
        raw.append(kind)
        for i, value in enumerate(row):
            a = row[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            p = a + b - c
            paeth = (a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c)
                     else b if abs(p - b) <= abs(p - c) else c)
            raw.append((value - (0, a, b, (a + b) >> 1, paeth)[kind]) & 255)
        previous = bytes(row)
    header = struct.pack(">IIBBBBB", width, height, depth,
                         (6 if alpha else 2) if color_type is None else color_type, 0, 0, interlace)
    return (PNG_SIGNATURE + png_chunk(b"IHDR", header) +
            png_chunk(b"IDAT", zlib.compress(bytes(raw))) + png_chunk(b"IEND", b""))


def hex_rgb(value):
    return tuple(bytes.fromhex(value[1:]))


def page_pixels(background, *regions, width=320, height=180):
    """A capture-sized page: `background`, then (x0, y0, x1, y1, color) regions."""
    pixels = [hex_rgb(background)] * (width * height)
    for x0, y0, x1, y1, color in regions:
        for y in range(y0, y1):
            pixels[y * width + x0:y * width + x1] = [hex_rgb(color)] * (x1 - x0)
    return pixels


def palette_of(pixels, width=320, height=180):
    image = encode_png(width, height, pixels, filters=(0, 1, 2, 3, 4))
    return capsule.rendered_palette(capsule.png_colors(image))


def event_page(primary, secondary):
    """The round-069 layout at capture size: a header gradient, two white
    cards with buttons and body text, and the Show sold out control."""
    return page_pixels(
        "#f5f7f5",
        (0, 0, 160, 30, primary), (160, 0, 320, 30, secondary),
        (60, 40, 260, 90, "#ffffff"), (60, 100, 260, 150, "#ffffff"),
        (70, 60, 200, 62, "#333333"), (70, 120, 200, 122, "#333333"),
        (70, 75, 100, 85, secondary), (70, 135, 100, 145, secondary),
        (110, 160, 210, 172, primary),
    )


class PaletteTests(unittest.TestCase):
    def test_png_decoder_matches_every_filter_and_channel_layout(self):
        width, height = 23, 11
        seed = hashlib.sha256(b"palette").digest() * 64
        # Few distinct colors, so neighbouring rows and pixels both repeat
        # and differ; every filter type sees both cases.
        pixels = [tuple(seed[(i * 7 + k) % 97] // 64 * 85 for k in range(3)) for i in range(width * height)]
        expected = collections.Counter(pixels)
        for alpha in (False, True):
            for filters in ((0,), (1,), (2,), (3,), (4,), (4, 3, 2, 1, 0)):
                with self.subTest(alpha=alpha, filters=filters):
                    image = encode_png(width, height, pixels, alpha=alpha, filters=filters)
                    self.assertEqual(capsule.png_colors(image), expected)

    def test_png_decoder_rejects_unsupported_or_malformed_input(self):
        pixels = [(1, 2, 3)] * 4
        good = encode_png(2, 2, pixels)
        self.assertEqual(capsule.png_colors(good), collections.Counter({(1, 2, 3): 4}))
        header = lambda width, height: png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        unsupported = [
            None, b"", b"GIF89a", good[:8], good[:30],
            PNG_SIGNATURE + good[8 + 25:],                             # no IHDR
            encode_png(2, 2, pixels, depth=16),
            encode_png(2, 2, pixels, color_type=3),
            encode_png(2, 2, pixels, color_type=0),
            encode_png(2, 2, pixels, interlace=1),
            PNG_SIGNATURE + header(4000, 4000),                        # too large
            PNG_SIGNATURE + header(2, 2) + png_chunk(b"IDAT", zlib.compress(b"\x05" + bytes(6) + b"\x00" + bytes(6))),
            PNG_SIGNATURE + header(2, 2) + png_chunk(b"IDAT", zlib.compress(b"\x00" + bytes(6))),  # short
            PNG_SIGNATURE + header(2, 2) + png_chunk(b"IDAT", b"not zlib"),
            PNG_SIGNATURE + png_chunk(b"IHDR", b"short"),
        ]
        for image in unsupported:
            with self.subTest(image=image[:40] if image else image):
                with self.assertRaises(protocol.Invalid):
                    capsule.png_colors(image)

    def test_color_families(self):
        table = {
            "#ffc107": "amber", "#ffbf00": "amber", "#ffa000": "amber", "#f59e0b": "amber",
            "#ff9800": "orange", "#d97706": "orange", "#ff5722": "orange",
            "#ffeb3b": "yellow", "#ffd700": "yellow",
            "#2d5a3d": "green", "#4a7c59": "green", "#4caf50": "green",
            "#008080": "teal", "#1976d2": "blue", "#0f172a": "blue",
            "#7b1fa2": "purple", "#bb86fc": "purple",
            "#e91e63": "pink", "#ffcdd2": "pink", "#c41e3a": "red", "#d32f2f": "red",
            "#795548": "brown", "#8b4513": "brown",
            "#ffffff": "white", "#f5f7f5": "white", "#eeeeee": "light gray", "#cccccc": "light gray",
            "#a3bcaa": "gray", "#666666": "gray", "#333333": "dark gray",
            "#1e1e1e": "black", "#121212": "black", "#000000": "black",
        }
        for color, name in table.items():
            with self.subTest(color=color):
                self.assertEqual(capsule.palette_bucket(*hex_rgb(color))[0], name)
        self.assertEqual(set(table.values()), set(capsule.PALETTE_NAMES))
        # Neutrals are named by lightness alone; each hue splits into bands.
        self.assertNotEqual(capsule.palette_bucket(*hex_rgb("#2d5a3d")), capsule.palette_bucket(*hex_rgb("#4a7c59")))
        self.assertEqual(capsule.palette_bucket(*hex_rgb("#ffa000")), capsule.palette_bucket(*hex_rgb("#ffc107")))

    def test_green_page_reports_green_and_no_amber(self):
        self.assertEqual(palette_of(event_page("#2d5a3d", "#4a7c59")), [
            {"name": "white", "hex": "#f5f7f5", "percent": 79},
            {"name": "green", "hex": "#2d5a3d", "percent": 10},
            {"name": "green", "hex": "#4a7c59", "percent": 9},
            {"name": "dark gray", "hex": "#333333", "percent": 1},
        ])

    def test_amber_page_reports_amber(self):
        # Amber shades of one lightness band share a bucket; its hex is the
        # most painted exact color.
        self.assertEqual(palette_of(event_page("#ffa000", "#ffc107")), [
            {"name": "white", "hex": "#f5f7f5", "percent": 79},
            {"name": "amber", "hex": "#ffa000", "percent": 20},
            {"name": "dark gray", "hex": "#333333", "percent": 1},
        ])

    def test_dark_mode_page(self):
        pixels = page_pixels(
            "#121212", (0, 0, 320, 40, "#1e1e1e"), (40, 60, 280, 100, "#2c2c2c"),
            (40, 110, 280, 150, "#2c2c2c"), (60, 75, 200, 78, "#e0e0e0"), (60, 125, 200, 128, "#e0e0e0"),
            (110, 158, 210, 176, "#bb86fc"),
        )
        self.assertEqual(palette_of(pixels), [
            {"name": "black", "hex": "#121212", "percent": 64},
            {"name": "dark gray", "hex": "#2c2c2c", "percent": 32},
            {"name": "purple", "hex": "#bb86fc", "percent": 3},
            {"name": "light gray", "hex": "#e0e0e0", "percent": 1},
        ])

    def test_ranking_is_deterministic_bounded_and_rounded(self):
        counts = collections.Counter({
            hex_rgb("#ffffff"): 300, hex_rgb("#fafafa"): 300,     # tie inside one bucket
            hex_rgb("#1976d2"): 150, hex_rgb("#d32f2f"): 150,     # tie between buckets
            hex_rgb("#4caf50"): 40, hex_rgb("#ffc107"): 30, hex_rgb("#7b1fa2"): 20,
            hex_rgb("#e91e63"): 6, hex_rgb("#008080"): 4,          # beyond the limit
        })
        palette = capsule.rendered_palette(counts)
        self.assertEqual(palette, capsule.rendered_palette(collections.Counter(dict(reversed(counts.items())))))
        self.assertEqual([(c["name"], c["hex"], c["percent"]) for c in palette], [
            ("white", "#fafafa", 60), ("blue", "#1976d2", 15), ("red", "#d32f2f", 15),
            ("green", "#4caf50", 4), ("amber", "#ffc107", 3), ("purple", "#7b1fa2", 2)])
        self.assertEqual(len(capsule.rendered_palette(counts, limit=3)), 3)
        tiny = collections.Counter({hex_rgb("#ffffff"): 996, hex_rgb("#ffc107"): 4})
        self.assertEqual([c["name"] for c in capsule.rendered_palette(tiny)], ["white"], "under 1% is not listed")
        with self.assertRaises(protocol.Invalid):
            capsule.rendered_palette(collections.Counter())


class PaletteCaptureTests(unittest.TestCase):
    GREEN = encode_png(4, 2, [hex_rgb("#f5f7f5")] * 5 + [hex_rgb("#2d5a3d")] * 3, filters=(2, 4))

    def run_scripted(self, palette, load_errors=()):
        browser = ScriptedBrowser(load_errors=load_errors, palette=palette)
        data = bundle("<p id=item hidden></p><button id=show>Show</button>", [
            step("assert-hidden", "#item"), step("click", "#show"), step("assert-visible", "#item")])
        browser.site = data["request"]["siteId"]
        return browser, capsule.run_browser(data, playwright_factory=browser)

    def test_palette_is_separate_evidence_from_a_fresh_desktop_context(self):
        errors = [PageError("SecurityError", STORAGE_ERROR)]
        palette = PaletteDouble(self.GREEN)
        browser, result = self.run_scripted(palette, errors)
        # Rounded shares may exceed 100 by at most one half per entry.
        self.assertEqual(result["renderedColors"], {"viewport": {"width": 1280, "height": 720}, "colors": [
            {"name": "white", "hex": "#f5f7f5", "percent": 63},
            {"name": "green", "hex": "#2d5a3d", "percent": 38}]})
        self.assertEqual(palette.kwargs, {"viewport": {"width": 1280, "height": 720}, "device_scale_factor": 0.25,
                                          "service_workers": "block", "accept_downloads": False})
        self.assertEqual(palette.shot, {"type": "png", "scale": "device", "timeout": capsule.PALETTE_TIMEOUT_MS})
        # The same request guard, popup, download and websocket handling, but
        # no page-error listener: this load never adds step evidence.
        self.assertEqual([route.outcome for route in palette.routes], ["continued"])
        self.assertTrue({"on:page", "on:download", "on:websocket"} <= set(palette.calls))
        self.assertNotIn("on:pageerror", palette.calls)
        self.assertEqual(result["pageErrors"]["count"], 1)
        # The step context is closed, its receipt final, before the palette
        # context exists; the palette context is closed too.
        self.assertLess(browser.calls.index("close"), browser.calls.index("new_context:palette"))
        self.assertEqual(palette.calls[-1], "close")
        _, without = self.run_scripted(PaletteDouble(None), errors)
        self.assertEqual({k: v for k, v in result.items() if k != "renderedColors"}, without)
        self.assertEqual(result["status"], "passed")
        self.assertLessEqual(len(protocol.canonical(result)), protocol.MAX_RESULT)

    def test_palette_is_omitted_when_capture_fails_or_is_blocked(self):
        cases = {
            "no image": PaletteDouble(None),
            "not a png": PaletteDouble(b"<svg/>"),
            "screenshot timeout": PaletteDouble(self.GREEN, error=TimeoutError("screenshot")),
            "foreign request": PaletteDouble(self.GREEN, during_load=[("http://203.0.113.9/font.woff2", False)]),
            "navigation": PaletteDouble(self.GREEN, during_load=[("/elsewhere.html", True)]),
            "frame moved": PaletteDouble(self.GREEN, frame_url="http://127.0.0.1:9/other/"),
        }
        for label, palette in cases.items():
            with self.subTest(label):
                _, result = self.run_scripted(palette)
                self.assertNotIn("renderedColors", result)
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["blockedRequests"], [], "palette blocks never reach step evidence")
                self.assertEqual(palette.calls[-1], "close")
                if palette.during_load:
                    self.assertEqual(palette.routes[-1].outcome, "aborted")

    def test_palette_never_starts_late_in_the_capsule_deadline(self):
        palette = PaletteDouble(self.GREEN)
        with patch.object(capsule, "PALETTE_START_BUDGET_S", 0):
            _, result = self.run_scripted(palette)
        self.assertNotIn("renderedColors", result)
        self.assertEqual(result["status"], "passed")
        self.assertIsNone(palette.kwargs, "no palette context was created")
        # A late capture (at most two timeouts) still ends well inside 45 s.
        self.assertLess(capsule.PALETTE_START_BUDGET_S + 2 * capsule.PALETTE_TIMEOUT_MS / 1000, 40)

    def test_broker_relays_rendered_colors_unchanged(self):
        data = bundle("hi")
        request = data["request"]
        receipt = {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed",
                   "siteId": request["siteId"], "sha256": request["sha256"],
                   "planSha256": protocol.plan_hash(request), "viewport": request["viewport"],
                   "steps": [], "diagnostics": {}, "blockedRequests": [],
                   "renderedColors": {"viewport": {"width": 1280, "height": 720}, "colors": [
                       {"name": "white", "hex": "#f5f7f5", "percent": 75}]},
                   "scope": protocol.SCOPE}
        config = {"docker": "/usr/bin/docker", "imageId": "sha256:" + "a" * 64,
                  "ownerUid": os.getuid(), "transport": "local", "snapshotRoot": "/owned"}
        with (
            patch.object(broker, "snapshot_bundle", return_value=data),
            patch.object(broker, "bounded_process",
                         side_effect=lambda argv, *_a, **_k: protocol.canonical(receipt) if "run" in argv else b""),
        ):
            self.assertEqual(broker.inspect_request(request, config), receipt)


def palette_names(result):
    return [color["name"] for color in result["renderedColors"]["colors"]]


def palette_share(result, name):
    return sum(c["percent"] for c in result["renderedColors"]["colors"] if c["name"] == name)


TOWER1_PLAN = [step("assert-hidden", "#midnight-concert-card"), role_step("click", "button", "Show sold out"),
               step("assert-visible", "#midnight-concert-card")]

# Fleet rounds 106 and 107 (ods-main-qualification-20260924-r049): the
# model-authored bytes of recorded publications, from the replay fixtures of
# the plugin tests, checked against each host snapshot digest.
PIXEL_TESTS = Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/tests"
MIDNIGHT_OWNER = ("heading", "Midnight sold-out concert")


def recorded_site(fixture_name, publication):
    fixture = json.loads((PIXEL_TESTS / fixture_name).read_text(encoding="utf-8"))
    files, receipt = {}, None
    for call in fixture["turns"][0]["calls"]:
        if call["call"] == publication:
            receipt = call["details"]
            break
        name = call["arguments"].get("path", "").split("/")[-1]
        if call["tool"] == "write":
            files[name] = call["arguments"]["content"]
        elif call["tool"] == "edit":
            for edit in call["arguments"]["edits"]:
                files[name] = files[name].replace(edit["oldText"], edit["newText"], 1)
    files = {name: content.encode() for name, content in files.items()}
    assert bundle(None, None, files)["request"]["sha256"] == receipt["sha256"], fixture_name
    return files, fixture


@unittest.skipUnless(
    os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1", "real Chromium opt in"
)
class BrowserTests(unittest.TestCase):
    def check(self, html, steps, files=None):
        # Fixture browsers get a separate process group and deadline too. The
        # production caller uses the stricter Docker capsule, never this path.
        import subprocess
        import signal

        script = str(Path(capsule.__file__).resolve())
        child = subprocess.Popen(
            [sys.executable, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            output, error = child.communicate(
                protocol.canonical(bundle(html, steps, files)), timeout=20
            )
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
            return protocol.strict_json(output)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)

    def fade_fixture(self, animation):
        return (
            '<style>@keyframes reveal{from{opacity:.1}to{opacity:1}}'
            '@keyframes disappear{from{opacity:1}to{opacity:0}}'
            f'.revealed{{animation:{animation}}}</style>'
            '<article id="item" hidden>Sold out</article>'
            '<button onclick="const e=document.querySelector(\'#item\');'
            'e.hidden=false;e.className=\'revealed\'">Reveal</button>'
        )

    def reveal_steps(self):
        return [step("assert-hidden", "#item"), step("click", name="Reveal"),
                step("assert-visible", "#item")]

    def test_finite_reveal_fade_settles(self):
        result = self.check(self.fade_fixture("reveal .6s linear forwards"), self.reveal_steps())
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["steps"][-1]["before"]["opacity"], "1")

    def test_infinite_fade_remains_unstable(self):
        result = self.check(self.fade_fixture("reveal 20s linear infinite alternate"), self.reveal_steps())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["steps"][-1]["errorCode"], "unstable")

    def test_fade_to_hidden_is_not_visible(self):
        result = self.check(self.fade_fixture("disappear .6s linear forwards"), self.reveal_steps())
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["steps"][-1]["errorCode"], "visibility_mismatch")
        self.assertFalse(result["steps"][-1]["before"]["visible"])

    def test_hidden_flex(self):
        html = '<style>.card{display:flex}</style><article hidden class="card" id="item">Sold out</article>'
        result = self.check(html, [step("assert-hidden", "#item")])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["steps"][0]["before"]["visible"])
        self.assertEqual(result["diagnostics"]["renderedHiddenAttributeCount"], 1)
        html += "<style>.card[hidden]{display:none}</style><button onclick=\"document.querySelector('#item').hidden=false\">Reveal</button>"
        result = self.check(
            html,
            [
                step("assert-hidden", "#item"),
                step("click", name="Reveal"),
                step("assert-visible", "#item"),
            ],
        )
        self.assertEqual(result["status"], "passed", result)

    def test_accessible_name_prefix(self):
        html = '<style>button::before{content:"\\2193 "}</style><button>Show items</button>'
        result = self.check(html, [step("click", name="Show items")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["steps"][0]["before"]["count"], 0)
        result = self.check(
            html.replace("<button>", '<button aria-label="Show items">'),
            [step("click", name="Show items")],
        )
        self.assertEqual(result["status"], "passed", result)

    def test_unicode_accessible_name(self):
        result = self.check(
            "<button>Mostrar pr\u00f3ximos eventos</button>",
            [step("click", name="Mostrar pr\u00f3ximos eventos")],
        )
        self.assertEqual(result["status"], "passed", result)

    def test_visibility_scope(self):
        result = self.check(
            '<div id="item" style="opacity:0">hidden</div>',
            [step("assert-hidden", "#item")],
        )
        self.assertEqual(result["status"], "passed")
        for style in ("clip-path:inset(100%)", "position:absolute;left:-10000px", ""):
            result = self.check(
                f'<div id="item" style="{style}">layout visible</div><div style="position:fixed;inset:0;background:black"></div>',
                [step("assert-visible", "#item")],
            )
            self.assertEqual(result["status"], "passed", result)
            self.assertIn("not pixel paint, occlusion, clipping", result["scope"])

    def test_unknown_selector(self):
        result = self.check("<p>empty</p>", [step("assert-hidden", "#missing")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["steps"][0]["errorCode"], "no_match")
        self.assertEqual(result["steps"][0]["before"], {"count": 0})
        result = self.check("<p class=x>a</p><p class=x>b</p>", [step("assert-hidden", ".x")])
        self.assertEqual(result["steps"][0]["errorCode"], "selector_not_unique")
        self.assertEqual(result["steps"][0]["before"], {"count": 2})

    def test_invalid_css_retains_failing_step_and_recovery_checks(self):
        html = ('<h1>Fixture events</h1><article class="event-card">'
                '<span class="event-badge">Dawn Jazz</span></article>'
                '<article id="midnight-concert" hidden>Sold out</article>'
                '<button onclick="document.querySelector(\'#midnight-concert\').hidden=false">Show sold out</button>')
        heading = {"action": "assert-visible", "locator": {
            "role": "heading", "name": "Fixture events", "exact": True}}
        invalid = ".event-card:has(.event-badge:contains('Dawn Jazz'))"
        interaction = [step("assert-hidden", "#midnight-concert"),
                       step("click", name="Show sold out"),
                       step("assert-visible", "#midnight-concert")]
        result = self.check(html, [heading, step("assert-visible", invalid), *interaction])
        self.assertEqual(result["status"], "failed", result)
        self.assertNotIn("errorCode", result)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["status"], "passed")
        self.assertEqual(result["steps"][1], {"index": 1,
            **step("assert-visible", invalid), "stable": False,
            "status": "failed", "errorCode": "invalid_selector"})
        self.assertEqual(result["blockedRequests"], [])
        corrected = self.check(html, [heading,
            step("assert-visible", ".event-card:has(.event-badge)"), *interaction])
        self.assertEqual(corrected["status"], "passed", corrected)
        self.assertEqual(corrected["sha256"], result["sha256"])
        self.assertEqual(len(corrected["steps"]), 5)

    def test_invalid_click_selector_produces_no_measurement_or_click(self):
        result = self.check('<button onclick="location.href=\'next.html\'">Next</button>',
                            [step("click", "button:contains('Next')")])
        self.assertEqual(result["steps"][0]["errorCode"], "invalid_selector")
        self.assertNotIn("before", result["steps"][0])
        self.assertNotIn("after", result["steps"][0])
        self.assertEqual(result["blockedRequests"], [])

    def test_author_script_cannot_spoof_selector_parser_failure(self):
        html = ('<p id="item">visible</p><script>Document.prototype.querySelectorAll=()=>'
                '{throw new DOMException("spoofed", "SyntaxError")};</script>')
        result = self.check(html, [step("assert-visible", "#item")])
        self.assertEqual(result["status"], "passed", result)

    def test_spoofing(self):
        html = '<p id="item" style="display:none">hidden</p><script>window.getComputedStyle=()=>({display:"block"});Element.prototype.checkVisibility=()=>true;Document.prototype.querySelectorAll=()=>[document.body];</script>'
        result = self.check(html, [step("assert-hidden", "#item")])
        self.assertEqual(result["status"], "passed", result)

    def test_external_network_never_reaches_listener(self):
        import http.server
        import threading

        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            html = f'<p id="item">safe</p><script>fetch("http://127.0.0.1:{server.server_port}/secret").catch(()=>{{}})</script>'
            result = self.check(html, [step("assert-visible", "#item")])
            self.assertEqual(hits, [])
            self.assertEqual(result["steps"][0]["status"], "passed")
        finally:
            server.shutdown()
            server.server_close()

    def test_intentional_hidden_override_diagnostic_only(self):
        result = self.check(
            '<p hidden id="item" style="display:block">intentionally visible</p>',
            [step("assert-visible", "#item")],
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["diagnostics"]["renderedHiddenAttributeCount"], 1)

    def test_navigation(self):
        result = self.check(
            "<button onclick=\"location.href='next.html'\">Next</button>",
            [step("click", name="Next")],
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("navigation", result["blockedRequests"])

    def test_fleet_hidden_card_by_role_and_name(self):
        result = self.check(FLEET_EVENTS_HTML, FLEET_PLAN)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["steps"][2]["before"]["count"], 1)
        self.assertFalse(result["steps"][2]["before"]["visible"])
        self.assertTrue(result["steps"][4]["before"]["visible"])
        # The reverse plan hides the same card again by the same locator.
        shown = FLEET_EVENTS_HTML.replace(' id="midnight-card" hidden>', ' id="midnight-card">').replace(
            ">Show sold out</button>", ">Hide sold out</button>")
        result = self.check(shown, [role_step("assert-visible", *MIDNIGHT),
                                    role_step("click", "button", "Hide sold out"),
                                    role_step("assert-hidden", *MIDNIGHT)])
        self.assertEqual(result["status"], "passed", result)

    def test_every_hiding_technique_the_fleet_tried(self):
        for hidden in ('<article hidden><h2>Midnight Sold-Out Concert</h2></article>',
                       '<style>.off{display:none}</style><article class=off><h2>Midnight Sold-Out Concert</h2></article>',
                       '<article style="visibility:hidden"><h2>Midnight Sold-Out Concert</h2></article>',
                       '<article style="visibility:hidden;opacity:0"><h2>Midnight Sold-Out Concert</h2></article>',
                       '<article aria-hidden="true" hidden><h2>Midnight Sold-Out Concert</h2></article>',
                       '<h2 hidden>Midnight Sold-Out Concert</h2>'):
            with self.subTest(hidden=hidden):
                result = self.check("<h2>Dawn Jazz</h2>" + hidden, [role_step("assert-hidden", *MIDNIGHT)])
                self.assertEqual(result["status"], "passed", result)

    def test_role_locator_semantics_outside_assert_hidden_are_unchanged(self):
        html = "<h2>Dawn Jazz</h2><article hidden><h2>Midnight Sold-Out Concert</h2></article>"
        result = self.check(html, [role_step("assert-visible", *MIDNIGHT)])
        self.assertEqual(result["steps"][0]["before"], {"count": 0})
        self.assertEqual(result["steps"][0]["errorCode"], "no_match")
        result = self.check(html, [role_step("assert-hidden", "heading", "Dawn Jazz")])
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch")
        result = self.check('<button hidden>Reveal</button>', [role_step("click", "button", "Reveal")])
        self.assertEqual(result["steps"][0]["before"], {"count": 0})

    def test_hidden_inclusive_matching_keeps_uniqueness_and_exact_names(self):
        card = "<article hidden><h2>Midnight Sold-Out Concert</h2></article>"
        for html, expected in ((card * 2, 2), (card + "<h2>Midnight Sold-Out Concert</h2>", 2),
                               (card.replace("Midnight", "Midnight "), 1),
                               (card.replace("Sold-Out", "sold-out"), 0),
                               ("<article hidden><h3 role=none>Midnight Sold-Out Concert</h3></article>", 0),
                               ("<template><h2>Midnight Sold-Out Concert</h2></template>", 0)):
            with self.subTest(html=html):
                result = self.check("<p>x</p>" + html, [role_step("assert-hidden", *MIDNIGHT)])
                self.assertEqual(result["steps"][0]["before"].get("count"), expected, result)
                if expected != 1:
                    self.assertEqual(result["steps"][0]["errorCode"],
                                     "no_match" if expected == 0 else "selector_not_unique")

    def test_author_script_cannot_spoof_hidden_role_matching(self):
        html = ("<article hidden><h2>Midnight Sold-Out Concert</h2></article><script>"
                "Element.prototype.getAttribute=()=>'heading';Document.prototype.querySelectorAll=()=>[];"
                "Element.prototype.querySelectorAll=()=>[];window.getComputedStyle=()=>({display:'block'});"
                "Object.defineProperty(Node.prototype,'textContent',{get(){return 'spoof'}});</script>")
        result = self.check(html, [role_step("assert-hidden", *MIDNIGHT)])
        self.assertEqual(result["status"], "passed", result)

    def test_hidden_role_matcher_agrees_with_playwright_include_hidden(self):
        # The capsule's isolated-world matcher is a port of Playwright's
        # getByRole(includeHidden) rules; compare against the pinned engine.
        from playwright.sync_api import sync_playwright

        html = (
            '<style>.off{display:none}.arrow::before{content:"\\2193  "}</style>'
            '<h2>Dawn Jazz</h2><article hidden><h2>Midnight Sold-Out Concert</h2></article>'
            '<div class=off><h3>Late Show</h3></div><h4 style="visibility:hidden">Ghost Tour</h4>'
            '<div aria-hidden="true"><button>Close banner</button></div>'
            '<button hidden aria-label="Open menu">&#9776;</button>'
            '<span id=lbl hidden>Filter events</span><button aria-labelledby="lbl" hidden>x</button>'
            '<label for=q>Search dates</label><input id=q type=text hidden>'
            '<label hidden><input type=checkbox> Only free events</label>'
            '<input type=submit value="Book now" hidden><input type=reset hidden>'
            '<a href="#x" hidden>Buy <strong>tickets</strong></a><a hidden>Plain</a>'
            '<button hidden><span>Show</span><div>sold out</div></button>'
            '<button class=arrow>Show items</button><button class=arrow hidden>Hidden items</button>'
            '<div role="tab" hidden>Schedule</div><span role="switch" aria-label="Dark mode" hidden></span>'
            '<h2 role="none" hidden>Not a heading</h2><div hidden><h2>Encore</h2></div><h2 hidden>Encore</h2>'
            '<a href="#t" title="Map" hidden></a>'
            '<label hidden>City <select><option>Oslo</option><option selected>Bergen</option></select></label>'
            '<h2>Tonight <span hidden>only</span></h2>'
            '<input type=radio aria-labelledby="lbl2" hidden><span id=lbl2 hidden>Seat <b>A</b></span>'
            '<div role="button" hidden><img alt="Star"> Favourite</div>'
            '<h5 hidden title="Tooltip heading"></h5><template><h2>Template</h2></template>'
            '<button hidden><svg width="8" height="8"><title>Dismiss</title></svg></button>'
            '<button hidden><span aria-hidden="true">✕</span> Close panel</button>'
        )
        queries = [
            ("heading", "Dawn Jazz"), ("heading", "Midnight Sold-Out Concert"), ("heading", "Late Show"),
            ("heading", "Ghost Tour"), ("button", "Close banner"), ("button", "Open menu"), ("button", "☰"),
            ("button", "Filter events"), ("textbox", "Search dates"), ("checkbox", "Only free events"),
            ("button", "Book now"), ("button", "Reset"), ("link", "Buy tickets"), ("link", "Plain"),
            ("button", "Show sold out"), ("button", "Show items"), ("button", "↓ Show items"),
            ("button", "Hidden items"), ("button", "↓ Hidden items"), ("tab", "Schedule"),
            ("switch", "Dark mode"), ("heading", "Not a heading"), ("heading", "Encore"), ("link", "Map"),
            ("combobox", "City Bergen"), ("combobox", "City"), ("heading", "Tonight only"),
            ("heading", "Tonight"), ("radio", "Seat A"), ("button", "Star Favourite"),
            ("heading", "Tooltip heading"), ("heading", "Template"), ("heading", "midnight sold-out concert"),
            ("heading", "Midnight  Sold-Out   Concert"), ("button", "Dismiss"), ("button", "✕ Close panel"),
            ("button", "Close panel"),
        ]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page()
                page.set_content("<!doctype html>" + html)
                cdp = page.context.new_cdp_session(page)
                frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
                world = cdp.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": "parity"})
                disagreements, matched = [], 0
                for role, name in queries:
                    expected = page.get_by_role(role, name=name, exact=True, include_hidden=True).count()
                    result = cdp.send("Runtime.callFunctionOn", {
                        "executionContextId": world["executionContextId"],
                        "functionDeclaration": capsule.ROLE_NAME_INCLUDING_HIDDEN,
                        "arguments": [{"value": role}, {"value": name}], "returnByValue": False})
                    self.assertNotIn("exceptionDetails", result, result)
                    actual = cdp.send("Runtime.callFunctionOn", {
                        "objectId": result["result"]["objectId"],
                        "functionDeclaration": "function(){return this.length}", "returnByValue": True,
                    })["result"]["value"]
                    matched += bool(expected)
                    if actual != expected:
                        disagreements.append((role, name, expected, actual))
                self.assertEqual(disagreements, [])
                self.assertGreaterEqual(matched, 20, "fixture must exercise real matches")
            finally:
                browser.close()

    def test_unguarded_storage_errors_are_reported_without_changing_steps(self):
        # Fleet round 054: every functional check passed while startup and a
        # click handler threw on the opaque-origin preview frame.
        html = ('<p id="item">visible</p><script>sessionStorage.getItem("seen")</script>'
                '<button onclick="localStorage.setItem(\'saved\', \'1\')">Save</button>')
        result = self.check(html, [step("assert-visible", "#item"), step("click", name="Save")])
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["pageErrors"]["count"], 2, result)
        self.assertEqual(result["pageErrors"]["messages"][0], "SecurityError: " + STORAGE_ERROR)
        self.assertIn("'localStorage'", result["pageErrors"]["messages"][1])

    def test_guarded_storage_reports_no_page_errors(self):
        html = ('<p id="item">visible</p><script>let seen;try{seen=sessionStorage.getItem("seen")}'
                'catch{seen=null}</script>')
        result = self.check(html, [step("assert-visible", "#item")])
        self.assertEqual(result["status"], "passed", result)
        self.assertNotIn("pageErrors", result)


    def test_rendered_palette_replays_tower1_round069(self):
        # The accent change only styles :focus outlines: nothing amber is painted.
        result = self.check(None, TOWER1_PLAN, tower1_files())
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(palette_names(result)[0], "white", result)
        self.assertNotIn("amber", palette_names(result))
        self.assertGreaterEqual(palette_share(result, "green"), 10, result)
        self.assertLessEqual({"#2d5a3d", "#4a7c59"}, {c["hex"] for c in result["renderedColors"]["colors"]})

    def test_rendered_palette_reports_the_amber_repair(self):
        result = self.check(None, TOWER1_PLAN, tower1_files(**AMBER_REPAIR))
        self.assertEqual(result["status"], "passed", result)
        self.assertGreaterEqual(palette_share(result, "amber"), 10, result)
        self.assertNotIn("green", palette_names(result))

    def test_rendered_palette_of_a_dark_page(self):
        result = self.check(DARK_PAGE_HTML, [role_step("assert-visible", "button", "Show sold out")])
        self.assertEqual(palette_names(result)[0], "black", result)
        self.assertGreaterEqual(palette_share(result, "black"), 50, result)
        self.assertIn("purple", palette_names(result))
        self.assertNotIn("white", palette_names(result))

    def test_rendered_palette_is_the_fresh_desktop_load(self):
        # Mobile media rules and the inspected click both paint other colors;
        # the palette is the page as first loaded at the desktop viewport.
        html = ("<style>body{margin:0;background:#1976d2}@media (max-width:600px){body{background:#d32f2f}}"
                "</style><button id=paint onclick=\"document.body.style.background='#ffc107'\">Paint</button>")
        result = self.check(html, [step("click", "#paint")])
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["viewport"], {"width": 375, "height": 812})
        self.assertEqual(result["renderedColors"]["viewport"], {"width": 1280, "height": 720})
        self.assertEqual(palette_names(result)[0], "blue", result)
        self.assertGreaterEqual(palette_share(result, "blue"), 95, result)
        self.assertFalse({"red", "amber"} & set(palette_names(result)), result)

    def test_palette_load_errors_are_not_step_evidence(self):
        html = ('<p id="item" style="background:#4caf50;height:600px">visible</p>'
                '<script>sessionStorage.getItem("seen")</script>')
        result = self.check(html, [step("assert-visible", "#item")])
        self.assertEqual(result["pageErrors"]["count"], 1, "the palette load is not recorded")
        self.assertIn("green", palette_names(result))

    def test_strixy_round107_corrected_args_pass(self):
        # The flattened call-8 steps, as the rejected request's ready args.
        files, _ = recorded_site("inspection-recovery-strixy-round107.json", 7)
        result = self.check(None, [step("assert-hidden", "#midnight-card"), step("click", name="Show sold out"),
                                   step("assert-visible", "#midnight-card")], files)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["steps"][0]["before"]["display"], "none")

    def test_mac_round106_heading_plan_passes(self):
        # Calls 9-12 failed on locators; the requirement-derived heading plan,
        # with the model's .reveal-btn click or the owner's button, passes.
        files, _ = recorded_site("inspection-recovery-mac-round106.json", 8)
        for control in (step("click", ".reveal-btn"), step("click", name="Show sold out")):
            with self.subTest(control=control):
                result = self.check(None, [role_step("assert-hidden", *MIDNIGHT_OWNER), control,
                                           role_step("assert-visible", *MIDNIGHT_OWNER)], files)
                self.assertEqual(result["status"], "passed", result)

    def test_laptop_round107_measured_failures_are_page_defects(self):
        # site-ba3eb2f6 never hides the card; the corrected plan fails at step 1.
        files, _ = recorded_site("inspection-recovery-laptop-round107.json", 3)
        result = self.check(None, [step("assert-hidden", ".event-card.sold-out"), step("click", ".show-sold-out-btn"),
                                   step("assert-visible", ".event-card.sold-out")], files)
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch", result)
        self.assertTrue(result["steps"][0]["before"]["visible"])
        # site-1f5f2cf8, call 26: scripts ran and the real click removed .hidden,
        # while .sold-out-card {display:none} still hides the card.
        files, fixture = recorded_site("inspection-recovery-laptop-round107.json", 23)
        recorded = next(call for call in fixture["turns"][0]["calls"] if call["call"] == 26)
        result = self.check(None, recorded["arguments"]["steps"], files)
        self.assertEqual([s["status"] for s in result["steps"]], ["passed", "passed", "passed", "failed"], result)
        self.assertEqual(result["steps"][3]["errorCode"], "visibility_mismatch")
        self.assertEqual(result["steps"][3]["before"]["display"], "none")
        self.assertEqual(recorded["details"]["steps"][3]["before"]["display"], "none")

    def test_laptop_round107_uppercase_control_needs_its_css_locator(self):
        # site-ba3eb2f6 repaired to start hidden: .show-sold-out-btn is
        # text-transform: uppercase, so the exact role/name click matches
        # nothing, and the published id offered in its place passes.
        files, _ = recorded_site("inspection-recovery-laptop-round107.json", 3)
        html = files["index.html"].decode()
        repaired = html.replace('<article class="event-card sold-out">', '<article class="event-card sold-out hidden">', 1)
        self.assertNotEqual(repaired, html)
        files = {"index.html": repaired.encode()}
        card = ".event-card.sold-out"
        result = self.check(None, [step("assert-hidden", card), step("click", name="Show sold out"), step("assert-visible", card)], files)
        self.assertEqual(result["steps"][1]["errorCode"], "no_match", result)
        result = self.check(None, [step("assert-hidden", card), step("click", "#showSoldOutBtn"), step("assert-visible", card)], files)
        self.assertEqual(result["status"], "passed", result)

    def test_laptop_round107_item_target_measures_the_broken_toggle(self):
        # site-c54d9d87 (call 13): the card has the hidden attribute and the
        # handler removes a class. The CSS item target is measured after the
        # click; the role/name heading was only a no_match.
        files, _ = recorded_site("inspection-recovery-laptop-round107.json", 12)
        item = "article.event-card.sold-out"
        result = self.check(None, [step("assert-hidden", item), step("click", ".show-sold-out-btn"), step("assert-visible", item)], files)
        self.assertEqual([s["status"] for s in result["steps"]], ["passed", "passed", "failed"], result)
        self.assertEqual(result["steps"][2]["errorCode"], "visibility_mismatch")
        self.assertEqual(result["steps"][2]["before"]["display"], "none")
        heading = ("heading", "Midnight Sold-Out Concert")
        result = self.check(None, [role_step("assert-hidden", *heading), step("click", ".show-sold-out-btn"), role_step("assert-visible", *heading)], files)
        self.assertEqual(result["steps"][2]["errorCode"], "no_match", result)

    def test_laptop_round107_call24_css_control_reaches_the_measurement(self):
        # site-1f5f2cf8 (call 24): the recorded role/name click matched nothing;
        # the offered #showSoldOutBtn plan clicks and measures .sold-out-card.
        files, fixture = recorded_site("inspection-recovery-laptop-round107.json", 23)
        recorded = next(call for call in fixture["turns"][0]["calls"] if call["call"] == 24)
        result = self.check(None, recorded["arguments"]["steps"], files)
        self.assertEqual(result["steps"][2]["errorCode"], "no_match", result)
        card = ".sold-out-card"
        result = self.check(None, [step("assert-hidden", card), step("click", "#showSoldOutBtn"), step("assert-visible", card)], files)
        self.assertEqual([s["status"] for s in result["steps"]], ["passed", "passed", "failed"], result)
        self.assertEqual(result["steps"][2]["before"]["display"], "none")

    def test_mac_round107_repair_steps(self):
        # The steps named for the new snapshot: the published heading around
        # the model's .reveal-btn click. They fail on the recorded page and
        # pass once the handler toggles the class the CSS hides.
        files, _ = recorded_site("inspection-recovery-mac-round107.json", 2)
        heading = ("heading", "Midnight Sold-out Concert")
        plan = [role_step("assert-hidden", *heading), step("click", ".reveal-btn"), role_step("assert-visible", *heading)]
        result = self.check(None, plan, files)
        self.assertEqual(result["steps"][2]["errorCode"], "no_match", result)
        html = files["index.html"].decode()
        for old, new in (("const isHidden = hiddenCard.style.display === 'none';", "const isHidden = hiddenCard.classList.contains('hidden');"),
                         ("hiddenCard.style.display = 'flex';", "hiddenCard.classList.remove('hidden');"),
                         ("hiddenCard.style.display = 'none';", "hiddenCard.classList.add('hidden');")):
            self.assertIn(old, html)
            html = html.replace(old, new, 1)
        result = self.check(None, plan, {"index.html": html.encode()})
        self.assertEqual(result["status"], "passed", result)

    def control_pages(self):
        yield "tower2", tower2_r100_files()
        yield "tower2-repaired", tower2_r100_files(repaired=True)
        for variant in CONTROL_REPLAY["variants"]["markup"]:
            yield variant, tower2_r100_files(variant=variant)
        for name, html in CONTROL_REPLAY["pages"].items():
            yield name, {"index.html": html.encode()}

    def assert_playwright_names(self, page, controls):
        # Each listed name is Playwright's own name for that element, in the
        # engine that addresses it: the fleet's default getByRole(role,
        # {name, exact: true}) for a control in the accessibility tree, and
        # includeHidden for a hidden one. A different own text is not the name.
        listed = collections.Counter()
        for item in controls["items"]:
            role = item["role"]
            element = page.get_by_role(role, include_hidden=True).nth(listed[role])
            listed[role] += 1
            exposed = element.and_(page.get_by_role(role)).count() == 1
            if item["visible"]:
                self.assertTrue(exposed, item)
            named = lambda name: element.and_(
                page.get_by_role(role, name=name, exact=True, include_hidden=not exposed)).count()
            self.assertEqual(named(item["name"]), 1, item)
            if "text" in item:
                self.assertEqual(named(item["text"]), 0, item)
        self.assertEqual(controls["count"], len(controls["items"]))
        for role in capsule.CONTROL_ROLES:
            self.assertEqual(page.get_by_role(role, include_hidden=True).count(), listed[role], role)

    def test_load_time_control_names_replay_fleet_round100(self):
        # The recorded plugin replay is this capsule's real output: tower2's
        # page reports the name its script set on load, not the button text.
        for name, files in self.control_pages():
            with self.subTest(page=name):
                result = self.check(None, TOWER2_R100_CSS_PLAN, files)
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["controls"], CONTROL_REPLAY["controls"][name])
        tower2 = self.check(None, TOWER2_R100_CSS_PLAN, tower2_r100_files())
        self.assertEqual(tower2["sha256"], CONTROL_REPLAY["publicationReceipt"]["sha256"])
        # The model's exact-name click on that snapshot matched nothing.
        role = CONTROL_REPLAY["calls"][0]["arguments"]["steps"]
        result = self.check(None, role, tower2_r100_files())
        self.assertEqual(result["steps"][1]["errorCode"], "no_match", result)
        self.assertEqual(result["controls"], CONTROL_REPLAY["controls"]["tower2"])
        repaired = self.check(None, role, tower2_r100_files(repaired=True))
        self.assertEqual(repaired["status"], "passed", repaired)
        # On the variants the load-time names are the exact requested name,
        # and the same exact-name click passes. The icon variant is the
        # exception for the click only: Chromium's own accessibility tree keeps
        # the space after the aria-hidden icon (" Show sold out") and role/name
        # steps match Chromium's name verbatim, while getByRole normalizes
        # whitespace and finds the button. Recorded so a change is noticed.
        for variant in CONTROL_REPLAY["variants"]["markup"]:
            with self.subTest(role_plan=variant):
                result = self.check(None, role, tower2_r100_files(variant=variant))
                self.assertEqual(result["controls"], CONTROL_REPLAY["controls"][variant])
                if variant == "aria-hidden-icon":
                    self.assertEqual(result["steps"][1]["errorCode"], "no_match", result)
                else:
                    self.assertEqual(result["status"], "passed", result)

    def test_load_time_names_are_after_scripts_before_steps_and_include_hidden(self):
        html = ('<a href="#top">Top</a><a>Not a link</a><div role="button" aria-labelledby="l">x</div>'
                '<span id="l" hidden>Open menu</span><dialog><button>Close</button></dialog>'
                '<button id="go" onclick="this.textContent=\'Clicked\'">Go</button>'
                '<input type="submit" value="Send"><script>document.getElementById("go").title="later"</script>'
                '<button id="late"></button><script>document.getElementById("late").textContent="Made by script"</script>'
                '<style>@keyframes in{from{opacity:0}to{opacity:1}}.fade{animation:in 30s}</style>'
                '<a class="fade" href="#t">Tickets</a><div aria-hidden="true"><button>Decor</button></div>')
        result = self.check(html, [step("click", "#go")])
        self.assertEqual(result["status"], "passed", result)
        # An entrance fade does not hide a control; aria-hidden does.
        self.assertEqual(result["controls"], {"count": 8, "items": [
            {"role": "link", "name": "Top", "visible": True, "source": "content"},
            {"role": "button", "name": "Open menu", "visible": True, "source": "aria-labelledby", "text": "x"},
            {"role": "button", "name": "Close", "visible": False, "source": "content"},
            {"role": "button", "name": "Go", "visible": True, "source": "content"},
            {"role": "button", "name": "Send", "visible": True, "source": "other"},
            {"role": "button", "name": "Made by script", "visible": True, "source": "content"},
            {"role": "link", "name": "Tickets", "visible": True, "source": "content"},
            {"role": "button", "name": "Decor", "visible": False, "source": "content"},
        ]})

    def test_load_time_names_agree_with_playwright_get_by_role(self):
        # The fleet checks getByRole(role, {name, exact: true}); every name
        # the capsule reports is that engine's name for that element, and a
        # replaced text is not. The variants' hidden decorations (an
        # aria-hidden icon or chevron, a hidden alternate label, a display:none
        # badge, an aria-hidden arrow) are not part of any name.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                for name, files in self.control_pages():
                    with self.subTest(page=name):
                        page = browser.new_page()
                        page.route("http://fixture.test/**", fixture_server(files))
                        page.goto("http://fixture.test/")
                        page.wait_for_timeout(100)
                        controls = CONTROL_REPLAY["controls"][name]
                        self.assert_playwright_names(page, controls)
                        rendered = sum(item["role"] == "button" and item["visible"] and item["name"] == "Show sold out"
                                       for item in controls["items"])
                        self.assertEqual(page.get_by_role("button", name="Show sold out", exact=True).count(), rendered)
                        page.close()
            finally:
                browser.close()

    def test_rendered_control_names_leave_out_hidden_descendants(self):
        # A control in the accessibility tree is named without its hidden
        # descendants, as Chromium and a default getByRole name it; a hidden
        # aria-labelledby target still contributes all of its text, and a
        # control that is itself hidden keeps its hidden-inclusive name.
        html = (
            '<style>.badge{display:none}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style>'
            '<button><span aria-hidden="true">🎫</span> Show sold out</button>'
            '<button>Show sold out<span aria-hidden="TRUE">▾</span></button>'
            '<button><span>Show sold out</span> <span hidden>Hide sold out</span></button>'
            '<button>Show sold out <span class="badge">(1)</span></button>'
            '<button>Show sold out<span style="visibility:hidden"> now</span></button>'
            '<a href="#events">All events <span aria-hidden="true">→</span></a>'
            '<button><span style="display:contents"><span aria-hidden="true">🎫</span>Tickets</span></button>'
            '<button><span aria-hidden="true">🎫</span><span class="sr">Buy tickets</span></button>'
            '<button>Save<span aria-hidden="false"> draft</span></button>'
            '<button><svg width="10" height="10"><title>Close</title><path d="M0 0h10"/></svg></button>'
            '<span id="filter" hidden>Filter <span aria-hidden="true">⚙</span>events</span>'
            '<button aria-labelledby="filter">x</button>'
            '<span id="sort">Sort <span aria-hidden="true">↕</span>events</span><button aria-labelledby="sort">x</button>'
            '<label for="share">Share <span hidden>this </span>page</label><button id="share">x</button>'
            '<dialog><button><span aria-hidden="true">✕</span> Close dialog</button></dialog>'
        )
        result = self.check(html, [step("assert-visible", "#share")])
        self.assertEqual(result["status"], "passed", result)
        controls = result["controls"]
        self.assertEqual(controls, {"count": 14, "items": [
            {"role": "button", "name": "Show sold out", "visible": True, "source": "content"},
            {"role": "button", "name": "Show sold out", "visible": True, "source": "content"},
            {"role": "button", "name": "Show sold out", "visible": True, "source": "content"},
            {"role": "button", "name": "Show sold out", "visible": True, "source": "content"},
            {"role": "button", "name": "Show sold out", "visible": True, "source": "content"},
            {"role": "link", "name": "All events", "visible": True, "source": "content"},
            {"role": "button", "name": "Tickets", "visible": True, "source": "content"},
            {"role": "button", "name": "Buy tickets", "visible": True, "source": "content"},
            {"role": "button", "name": "Save draft", "visible": True, "source": "content"},
            {"role": "button", "name": "Close", "visible": True, "source": "content"},
            {"role": "button", "name": "Filter ⚙events", "visible": True, "source": "aria-labelledby", "text": "x"},
            {"role": "button", "name": "Sort events", "visible": True, "source": "aria-labelledby", "text": "x"},
            {"role": "button", "name": "Share page", "visible": True, "source": "other", "text": "x"},
            {"role": "button", "name": "✕ Close dialog", "visible": False, "source": "content"},
        ]})
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page()
                page.set_content("<!doctype html>" + html)
                self.assert_playwright_names(page, controls)
                self.assertEqual(page.get_by_role("button", name="Show sold out", exact=True).count(), 5)
            finally:
                browser.close()

@unittest.skipUnless(
    os.environ.get("ODS_INSPECTION_TEST_IMAGE"), "isolated Docker image test opt in"
)
class DockerCapsuleTests(unittest.TestCase):
    def invoke(self, html, steps, cancel=False, files=None):
        import workspace_preview as publisher
        import threading
        import subprocess

        image = os.environ["ODS_INSPECTION_TEST_IMAGE"]
        self.assertRegex(image, r"^sha256:[a-f0-9]{64}$")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            site = workspace / "site"
            site.mkdir(mode=0o700)
            for name, data in (files or {"index.html": html.encode()}).items():
                (site / name).write_bytes(data)
                (site / name).chmod(0o600)
            previews = root / "previews"
            previews.mkdir(mode=0o700)
            receipt = publisher.publish_snapshot(
                workspace, previews, "site", os.getuid()
            )
            request = bundle(html, steps, files)["request"]
            request.update(siteId=receipt["siteId"], sha256=receipt["sha256"])
            config = {
                "docker": "/usr/bin/docker",
                "imageId": image,
                "ownerUid": os.getuid(),
                "transport": "local",
                "snapshotRoot": str(previews),
            }
            names = []
            real = broker.capsule_argv

            def argv(cfg, name):
                names.append(name)
                return real(cfg, name)

            event = threading.Event()
            if cancel:
                timer = threading.Timer(0.5, event.set)
                timer.start()
            try:
                with patch.object(broker, "capsule_argv", side_effect=argv):
                    if cancel:
                        with self.assertRaisesRegex(ValueError, "cancelled"):
                            broker.inspect_request(request, config, event)
                        result = None
                    else:
                        result = broker.inspect_request(request, config, event)
            finally:
                if cancel:
                    timer.cancel()
                for name in names:
                    remaining = subprocess.check_output(
                        [
                            *broker.docker_prefix(config),
                            "ps",
                            "-aq",
                            "--filter",
                            "name=^/" + name + "$",
                        ],
                        text=True,
                    )
                    self.assertEqual(
                        remaining.strip(), "", "owned capsule survived cleanup"
                    )
            return result

    def test_real_capsule_toggle(self):
        html = '<p id="item" hidden>hidden</p><button onclick="document.querySelector(\'#item\').hidden=false">Reveal</button>'
        result = self.invoke(
            html,
            [
                step("assert-hidden", "#item"),
                step("click", name="Reveal"),
                step("assert-visible", "#item"),
            ],
        )
        self.assertEqual(result["status"], "passed", result)

    def test_real_capsule_observed_bug(self):
        result = self.invoke(
            '<style>.card{display:flex}</style><p id="item" class="card" hidden>hidden</p>',
            [step("assert-hidden", "#item")],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["steps"][0]["errorCode"], "visibility_mismatch")

    def test_real_capsule_reports_storage_error(self):
        result = self.invoke(
            '<p id="item">visible</p><script>sessionStorage.getItem("seen")</script>',
            [step("assert-visible", "#item")],
        )
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["pageErrors"], {
            "count": 1, "messages": ["SecurityError: " + STORAGE_ERROR]})

    def test_real_capsule_hidden_card_by_role_and_name(self):
        result = self.invoke(FLEET_EVENTS_HTML, FLEET_PLAN)
        self.assertEqual(result["status"], "passed", result)

    def test_real_capsule_hung_script(self):
        start = time.monotonic()
        result = self.invoke(
            "<script>while(true){}</script>", [step("assert-visible", "body")]
        )
        self.assertEqual(result["status"], "failed")
        self.assertLess(time.monotonic() - start, 55)

    def test_real_capsule_cancel_cleanup(self):
        self.invoke(
            "<script>while(true){}</script>",
            [step("assert-visible", "body")],
            cancel=True,
        )


    def test_real_capsule_tower1_palette_replay(self):
        result = self.invoke(None, TOWER1_PLAN, files=tower1_files())
        self.assertEqual(result["status"], "passed", result)
        self.assertNotIn("amber", palette_names(result))
        self.assertGreaterEqual(palette_share(result, "green"), 10, result)
        repaired = self.invoke(None, TOWER1_PLAN, files=tower1_files(**AMBER_REPAIR))
        self.assertEqual(repaired["status"], "passed", repaired)
        self.assertGreaterEqual(palette_share(repaired, "amber"), 10, repaired)

if __name__ == "__main__":
    unittest.main()
