"""Boundaries and opt-in real browser regressions (ODS_PREVIEW_BROWSER_TESTS=1)."""

import base64
import collections
import copy
import hashlib
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


def bundle(html, steps=None, files=None, texts=None):
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
            **({"texts": texts} if texts else {}),
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

    def __init__(self, load_errors=(), click_errors=(), palette=None, text=None):
        self.load_errors, self.click_errors = list(load_errors), list(click_errors)
        self.handlers, self.calls, self.revealed = {}, [], False
        # Without a screenshot the capture fails and the palette is omitted.
        self.palette = palette or PaletteDouble(None)
        # The requested-text check has its own desktop context (TextDouble).
        self.text = text

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
        if self.text is not None and kwargs.get("viewport") == capsule.TEXT_VIEWPORT:
            self.calls.append("new_context:text")
            self.text.kwargs, self.text.site = kwargs, self.site
            return self.text
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


class TextDouble(PaletteDouble):
    """The requested-text context and page: a separate desktop load whose
    isolated world answers each measurement with the next scripted sample,
    {text: observation}, and records every scroll."""

    def __init__(self, samples, max_scroll=0, error=None, **kwargs):
        super().__init__(None, **kwargs)
        self.samples, self.max_scroll, self.check_error = list(samples), max_scroll, error
        self.asked, self.scrolls, self.waits, self.world = [], [], [], None

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)

    def new_cdp_session(self, _):
        return self

    def send(self, method, params=None):
        params = params or {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"childFrames": [{"frame": {"id": "T", "name": "inspection"}}]}}
        if method == "Page.createIsolatedWorld":
            self.world = params
            return {"executionContextId": 11}
        function = params["functionDeclaration"]
        argument = params["arguments"][0]["value"]
        if function == capsule.SCROLL_TO:
            self.scrolls.append(argument)
            return {"result": {"value": argument}}
        assert function == capsule.REQUESTED_TEXT, function
        self.asked.append(argument)
        if self.check_error:
            raise self.check_error
        sample = self.samples.pop(0) if len(self.samples) > 1 else self.samples[0]
        return {"result": {"value": {"results": [sample[text] for text in argument],
                                     "viewport": 720, "maxScroll": self.max_scroll}}}


VISIBLE = {"status": "visible"}
ABSENT = {"status": "absent"}
FOOTER_HIDDEN = {"status": "hidden", "element": "p", "reason": "display-none", "culprit": "div#soldOutSection"}
FADED = {"status": "hidden", "element": "section.reveal", "reason": "transparent"}


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


# Fleet round 087 (tower1, ODS 074db9bf): the owner asked for "a visible footer
# 'FLEET-eaa39f42e9 edited successfully'". The model put it inside the sold-out
# section, which is display:none until "Show sold out" is clicked, and answered
# that the footer was visible. The published bytes (sha256 e634cc7a…) are kept.
TOWER1_R087 = Path(__file__).resolve().parent / "fixtures/preview-requested-text/tower1-r087"
R087_FOOTER = "FLEET-eaa39f42e9 edited successfully"
R087_TITLE = "Night Garden FLEET-eaa39f42e9 Revised"
R087_PLAN = [step("assert-hidden", "#soldOutSection"), role_step("click", "button", "Show sold out"),
             step("assert-visible", "#soldOutSection")]


class RequestedTextTests(unittest.TestCase):
    def run_scripted(self, text, texts=(R087_FOOTER, R087_TITLE), palette=None):
        browser = ScriptedBrowser(palette=palette, text=text)
        data = bundle("<p id=item hidden></p><button id=show>Show</button>", [
            step("assert-hidden", "#item"), step("click", "#show"), step("assert-visible", "#item")],
            texts=list(texts))
        browser.site = data["request"]["siteId"]
        return browser, data["request"], capsule.run_browser(data, playwright_factory=browser)

    def test_texts_are_validated_bounded_and_outside_the_plan_hash(self):
        request = bundle("hi", texts=[R087_FOOTER, "Café – “quoted”"])["request"]
        protocol.validate_request(request)
        plan = {key: value for key, value in request.items() if key != "texts"}
        self.assertEqual(protocol.plan_hash(request), protocol.plan_hash(plan))
        for texts in ([], ["a"] * 2, ["x"] * 13, [" padded"], ["tab\tstop"], ["‮flip"], ["x" * 121],
                      ["é" * 121], [7], "text", None, [["nested"]]):
            with self.subTest(texts=texts), self.assertRaises(ValueError):
                protocol.validate_request({**plan, "texts": texts})
        protocol.validate_request({**plan, "texts": ["x" * 120] * 1})
        with self.assertRaises(ValueError):
            protocol.validate_request({**plan, "steps": [step("click", "#" + "s" * 250)] * 12,
                                       "texts": [f"{i}" + "t" * 119 for i in range(12)]})

    def test_publisher_export_never_receives_the_texts(self):
        data = bundle("hi", texts=[R087_FOOTER])
        request = data["request"]
        plan = {key: value for key, value in request.items() if key != "texts"}
        exported = {**data, "request": plan}
        receipt = {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed",
                   "siteId": request["siteId"], "sha256": request["sha256"],
                   "planSha256": protocol.plan_hash(request), "viewport": request["viewport"],
                   "steps": [], "diagnostics": {}, "blockedRequests": [], "scope": protocol.SCOPE}
        bodies = {}

        def process(argv, body, *_args, **_kwargs):
            if "exec" in argv:
                bodies["export"] = protocol.strict_json(body)
                return protocol.canonical(exported)
            if "run" in argv:
                bodies["capsule"] = protocol.strict_json(body)
                return protocol.canonical(receipt)
            return b""

        config = {"docker": "/usr/bin/docker", "imageId": "sha256:" + "a" * 64,
                  "ownerUid": os.getuid(), "transport": "native", "snapshotRoot": "/owned"}
        with (
            patch.object(broker, "docker_prefix", return_value=["/usr/bin/docker"]),
            patch.object(broker, "bounded_process", side_effect=process),
        ):
            self.assertEqual(broker.inspect_request(request, config), receipt)
        self.assertEqual(bodies["export"], plan)
        self.assertEqual(bodies["capsule"]["request"], request)

    def test_visible_at_load_needs_one_measurement_and_no_scroll(self):
        text = TextDouble([{R087_FOOTER: VISIBLE, R087_TITLE: VISIBLE}], max_scroll=4000)
        browser, _, result = self.run_scripted(text)
        self.assertEqual(result["requestedText"], {"viewport": {"width": 1280, "height": 720}, "scrolled": False,
                                                   "texts": [{"text": R087_FOOTER, "status": "visible"},
                                                             {"text": R087_TITLE, "status": "visible"}]})
        self.assertEqual(text.asked, [[R087_FOOTER, R087_TITLE]])
        self.assertEqual(text.scrolls, [])
        self.assertEqual(text.waits, [capsule.TEXT_SETTLE_MS])
        self.assertEqual(text.kwargs, {"viewport": {"width": 1280, "height": 720},
                                       "service_workers": "block", "accept_downloads": False})
        self.assertEqual(text.world["worldName"], "ods-requested-text")
        self.assertFalse(text.world["grantUniveralAccess"])
        # Same guard, no page-error listener; the step context closed first,
        # then the palette, then this context, which is closed too.
        self.assertTrue({"on:page", "on:download", "on:websocket"} <= set(text.calls))
        self.assertNotIn("on:pageerror", text.calls)
        self.assertLess(browser.calls.index("new_context:palette"), browser.calls.index("new_context:text"))
        self.assertLess(browser.calls.index("close"), browser.calls.index("new_context:palette"))
        self.assertEqual(text.calls[-1], "close")
        self.assertEqual(result["status"], "passed")

    def test_scroll_triggered_reveal_counts_as_visible(self):
        text = TextDouble([{"Our story": FADED}, {"Our story": FADED}, {"Our story": VISIBLE}], max_scroll=2000)
        _, _, result = self.run_scripted(text, texts=["Our story"])
        self.assertEqual(result["requestedText"]["texts"], [{"text": "Our story", "status": "visible"}])
        self.assertTrue(result["requestedText"]["scrolled"])
        # 80% of the 720 px view per step; it stops once the text is seen visible.
        self.assertEqual(text.scrolls, [576, 1152])
        self.assertEqual(text.waits, [capsule.TEXT_SETTLE_MS] + [capsule.TEXT_SCROLL_WAIT_MS] * 2)

    def test_text_hidden_through_the_whole_pass_is_reported_with_its_reason(self):
        text = TextDouble([{R087_FOOTER: FOOTER_HIDDEN, R087_TITLE: VISIBLE}, {R087_FOOTER: FOOTER_HIDDEN}],
                          max_scroll=500)
        _, _, result = self.run_scripted(text)
        self.assertEqual(result["requestedText"], {"viewport": {"width": 1280, "height": 720}, "scrolled": True,
                                                   "texts": [{"text": R087_FOOTER, **FOOTER_HIDDEN},
                                                             {"text": R087_TITLE, "status": "visible"}]})
        # Only the pending text is measured again: one scroll step, the final
        # settle, and back at the top.
        self.assertEqual(text.asked, [[R087_FOOTER, R087_TITLE]] + [[R087_FOOTER]] * 3)
        self.assertEqual(text.scrolls, [500, 0])
        self.assertEqual(text.waits, [capsule.TEXT_SETTLE_MS, capsule.TEXT_SCROLL_WAIT_MS,
                                      capsule.TEXT_FINAL_WAIT_MS, capsule.TEXT_SCROLL_WAIT_MS])
        # Separate evidence: the steps and status are those of a run without it.
        _, _, without = self.run_scripted(None, texts=())
        self.assertEqual({k: v for k, v in result.items() if k != "requestedText"}, without)

    def test_a_later_absent_sample_never_erases_a_hidden_one(self):
        text = TextDouble([{"Note": FADED}, {"Note": ABSENT}], max_scroll=0)
        _, _, result = self.run_scripted(text, texts=["Note"])
        self.assertEqual(result["requestedText"]["texts"], [{"text": "Note", **FADED}])
        # A one-screen page is not scrolled, but finite animations get the settle.
        self.assertEqual(text.scrolls, [])
        self.assertEqual(text.waits, [capsule.TEXT_SETTLE_MS, capsule.TEXT_FINAL_WAIT_MS])

    def test_scroll_pass_is_bounded_in_steps_and_time(self):
        text = TextDouble([{"Note": FADED}], max_scroll=10 ** 7)
        _, _, result = self.run_scripted(text, texts=["Note"])
        self.assertEqual(result["requestedText"]["texts"][0]["status"], "hidden")
        self.assertEqual(len(text.scrolls), capsule.TEXT_SCROLL_STEPS + 1)
        self.assertEqual(text.scrolls[-2:], [10 ** 7, 0])
        # Even a late start ends well inside the capsule deadline.
        worst = (capsule.TEXT_START_BUDGET_S + capsule.TEXT_TIMEOUT_MS / 1000 + capsule.TEXT_SCROLL_BUDGET_S +
                 (capsule.TEXT_SETTLE_MS + capsule.TEXT_FINAL_WAIT_MS + 2 * capsule.TEXT_SCROLL_WAIT_MS) / 1000)
        self.assertLess(worst, capsule.EVIDENCE_DEADLINE_S)
        with patch.object(capsule, "TEXT_SCROLL_BUDGET_S", -1):
            text = TextDouble([{"Note": FADED}], max_scroll=10 ** 7)
            self.run_scripted(text, texts=["Note"])
        self.assertEqual(text.scrolls, [], "no scroll step starts past the pass budget")

    def test_requested_text_is_omitted_when_the_check_fails_or_is_blocked(self):
        cases = {
            "isolated world error": TextDouble([{}], error=RuntimeError("target closed")),
            "invalid observation": TextDouble([{R087_FOOTER: {"status": "shown"}, R087_TITLE: VISIBLE}]),
            "unknown reason": TextDouble([{R087_FOOTER: {**FOOTER_HIDDEN, "reason": "tiny"}, R087_TITLE: VISIBLE}]),
            "malformed element name": TextDouble([{R087_FOOTER: {**FOOTER_HIDDEN, "element": 'p "quoted"'},
                                                   R087_TITLE: VISIBLE}]),
            "malformed color": TextDouble([{R087_FOOTER: {**FOOTER_HIDDEN, "reason": "same-color",
                                                          "colors": ["#NaNNaN", "#ffffff"]}, R087_TITLE: VISIBLE}]),
            "colors without same-color": TextDouble([{R087_FOOTER: {**FOOTER_HIDDEN, "colors": ["#ffffff", "#ffffff"]},
                                                      R087_TITLE: VISIBLE}]),
            "foreign request": TextDouble([{R087_FOOTER: VISIBLE, R087_TITLE: VISIBLE}],
                                          during_load=[("http://203.0.113.9/font.woff2", False)]),
            "frame moved": TextDouble([{R087_FOOTER: VISIBLE, R087_TITLE: VISIBLE}],
                                      frame_url="http://127.0.0.1:9/other/"),
        }
        for label, text in cases.items():
            with self.subTest(label):
                _, _, result = self.run_scripted(text)
                self.assertNotIn("requestedText", result)
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["blockedRequests"], [], "text-check blocks never reach step evidence")
                self.assertEqual(text.calls[-1], "close")

    def test_no_texts_or_a_late_start_runs_no_check(self):
        text = TextDouble([{R087_FOOTER: VISIBLE, R087_TITLE: VISIBLE}])
        _, _, result = self.run_scripted(text, texts=())
        self.assertNotIn("requestedText", result)
        self.assertIsNone(text.kwargs, "no requested-text context without texts")
        with patch.object(capsule, "TEXT_START_BUDGET_S", 0):
            _, _, late = self.run_scripted(text)
        self.assertNotIn("requestedText", late)
        self.assertIsNone(text.kwargs)

    def test_capsule_and_protocol_share_the_vocabulary(self):
        self.assertEqual(capsule.REQUESTED_TEXT_STATUSES, ("visible", "hidden", "absent", "unmeasured"))
        for reason in capsule.REQUESTED_TEXT_REASONS:
            self.assertIn(f"reason: '{reason}'", capsule.REQUESTED_TEXT)
        self.assertEqual(len(set(capsule.REQUESTED_TEXT_REASONS)), 9)
        # The measurement is read-only apart from the explicit scroll step.
        for mutation in ("classList.add", "setAttribute", ".style.", "innerHTML", "scrollTo", "scrollIntoView"):
            self.assertNotIn(mutation, capsule.REQUESTED_TEXT)
        self.assertIn("behavior: 'instant'", capsule.SCROLL_TO)


class ReceiptDeadlineTests(unittest.TestCase):
    SCRIPT = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import preview_inspection_capsule as capsule\n"
        "capsule.EVIDENCE_BUDGET_S = 0.5\n"
        "def stalled(bundle, checkpoint=None):\n"
        "    request = bundle['request']\n"
        "    receipt = {'schemaVersion': 1, 'kind': capsule.KIND, 'status': 'passed', 'siteId': request['siteId'],\n"
        "               'sha256': request['sha256'], 'planSha256': capsule.plan_hash(request), 'steps': [],\n"
        "               'scope': capsule.SCOPE}\n"
        "    checkpoint(receipt)\n"
        "    receipt['renderedColors'] = 'unfinished'\n"
        "    time.sleep(float(sys.argv[2]))\n"
        "    return {**receipt, 'requestedText': 'finished'}\n"
        "capsule.run_browser = stalled\n"
        "capsule.main()\n"
    )

    def run_capsule(self, stall):
        import subprocess

        host = str(Path(capsule.__file__).resolve().parent)
        started = time.monotonic()
        output = subprocess.run(
            [sys.executable, "-c", self.SCRIPT, host, str(stall)],
            input=protocol.canonical(bundle("hi", texts=[R087_FOOTER])),
            capture_output=True, timeout=30, check=True,
        ).stdout
        return output, time.monotonic() - started

    def test_a_stalled_evidence_load_still_delivers_the_final_step_receipt(self):
        output, elapsed = self.run_capsule(20)
        self.assertLess(elapsed, 10)
        self.assertEqual(output.count(b"\n"), 1)
        receipt = protocol.strict_json(output)
        self.assertEqual(receipt["status"], "passed")
        self.assertNotIn("renderedColors", receipt, "the checkpoint is a copy of the final step receipt")
        self.assertNotIn("requestedText", receipt)

    def test_a_finished_run_writes_its_own_receipt_once(self):
        output, _ = self.run_capsule(0)
        self.assertEqual(output.count(b"\n"), 1)
        self.assertEqual(protocol.strict_json(output)["requestedText"], "finished")


def palette_names(result):
    return [color["name"] for color in result["renderedColors"]["colors"]]


def palette_share(result, name):
    return sum(c["percent"] for c in result["renderedColors"]["colors"] if c["name"] == name)


TOWER1_PLAN = [step("assert-hidden", "#midnight-concert-card"), role_step("click", "button", "Show sold out"),
               step("assert-visible", "#midnight-concert-card")]

@unittest.skipUnless(
    os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1", "real Chromium opt in"
)
class BrowserTests(unittest.TestCase):
    def check(self, html, steps, files=None, texts=None):
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
                protocol.canonical(bundle(html, steps, files, texts)), timeout=20
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
            ("heading", "Midnight  Sold-Out   Concert"),
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

    def visibility(self, html, texts, files=None):
        result = self.check(html, [step("assert-visible", "body")], files, texts)
        self.assertEqual(result["status"], "passed", result)
        return result["requestedText"]

    def test_fade_in_on_scroll_is_visible_after_one_scroll_pass(self):
        html = ('<style>.reveal{opacity:0;transform:translateY(40px);transition:opacity .6s,transform .6s}'
                '.reveal.in{opacity:1;transform:none}</style><div style="height:2400px">Intro</div>'
                '<section class="reveal"><h2>Our story begins here</h2></section>'
                '<script>const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){'
                "e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.2});"
                "document.querySelectorAll('.reveal').forEach(el=>io.observe(el));</script>")
        evidence = self.visibility(html, ["Our story begins here"])
        self.assertTrue(evidence["scrolled"])
        self.assertEqual(evidence["texts"], [{"text": "Our story begins here", "status": "visible"}])

    def test_permanent_opacity_zero_is_not_visible(self):
        evidence = self.visibility('<style>.ghost{opacity:0}</style><footer class="ghost"><p>Always faded</p></footer>',
                                   ["Always faded"])
        self.assertEqual(evidence["texts"], [{"text": "Always faded", "status": "hidden", "element": "p",
                                              "reason": "transparent", "culprit": "footer.ghost"}])

    def test_display_none_ancestor_is_not_visible(self):
        evidence = self.visibility('<div id="later" style="display:none"><footer class="site-footer"><p>Inside hidden</p>'
                                   '</footer></div>', ["Inside hidden"])
        self.assertEqual(evidence["texts"], [{"text": "Inside hidden", "status": "hidden", "element": "p",
                                              "reason": "display-none", "culprit": "div#later"}])

    def test_white_on_white_is_not_visible(self):
        evidence = self.visibility('<style>body{background:#fff}footer{color:#fff}</style><main>Hi</main>'
                                   '<footer class="note">Ghost footer</footer>', ["Ghost footer"])
        self.assertEqual(evidence["texts"], [{"text": "Ghost footer", "status": "hidden", "element": "footer.note",
                                              "reason": "same-color", "culprit": "body",
                                              "colors": ["#ffffff", "#ffffff"]}])

    def test_normal_below_the_fold_text_is_visible_without_scrolling(self):
        evidence = self.visibility('<div style="height:2400px">Intro</div><footer>Bottom line</footer>', ["Bottom line"])
        self.assertEqual(evidence, {"viewport": {"width": 1280, "height": 720}, "scrolled": False,
                                    "texts": [{"text": "Bottom line", "status": "visible"}]})

    def test_painted_text_that_only_looks_risky_stays_visible(self):
        cases = {
            "gradient text": '<h1 style="background:linear-gradient(90deg,#f00,#00f);-webkit-background-clip:text;'
                             'background-clip:text;color:transparent">Painted</h1>',
            "white on a sibling backdrop": '<section style="position:relative;height:300px"><div style="position:absolute;'
                                           'inset:0;background:#123"></div><h1 style="position:relative;color:#fff">'
                                           'Painted</h1></section>',
            "white on a dark gradient": '<body style="background:linear-gradient(#111,#222);color:#fff"><h1>Painted</h1>',
            "fade-in on load": '<style>@keyframes f{from{opacity:0}to{opacity:1}}h1{animation:f .8s ease both}</style>'
                               '<h1>Painted</h1>',
            "inner scroll container": '<style>html,body{height:100%;margin:0;overflow:hidden}main{height:100%;'
                                      'overflow-y:auto}</style><main><div style="height:3000px"></div><p>Painted</p></main>',
            "split across inline elements": '<h1>Pain<span style="color:#c00">ted</span></h1>',
        }
        for label, html in cases.items():
            with self.subTest(label):
                self.assertEqual(self.visibility(html, ["Painted"])["texts"], [{"text": "Painted", "status": "visible"}])

    def test_other_hiding_techniques_are_named(self):
        cases = {
            "visibility": ('<div style="visibility:hidden"><span>Gone</span></div>', "visibility-hidden"),
            "screen-reader only": ('<span style="position:absolute;width:1px;height:1px;overflow:hidden;'
                                   'clip:rect(0,0,0,0)">Gone</span>', "clipped"),
            "off canvas": ('<nav style="position:fixed;top:0;left:0;width:300px;transform:translateX(-110%)">Gone</nav>',
                           "off-page"),
            "closed details": ('<details><summary>Question</summary><p>Gone</p></details>', "content-hidden"),
            "no size": ('<p style="font-size:0">Gone</p>', "zero-size"),
            "clear ink": ('<p style="color:transparent">Gone</p>', "transparent-text"),
        }
        for label, (html, reason) in cases.items():
            with self.subTest(label):
                [entry] = self.visibility(html, ["Gone"])["texts"]
                self.assertEqual((entry["status"], entry["reason"]), ("hidden", reason), entry)

    def test_round087_footer_inside_the_hidden_sold_out_section(self):
        files = {"index.html": (TOWER1_R087 / "index.html").read_bytes()}
        result = self.check(None, R087_PLAN, files, [R087_FOOTER, R087_TITLE])
        self.assertEqual(result["status"], "passed", "the show/hide steps still pass")
        self.assertEqual(result["requestedText"]["texts"], [
            {"text": R087_FOOTER, "status": "hidden", "element": "p", "reason": "display-none",
             "culprit": "div#soldOutSection"},
            {"text": R087_TITLE, "status": "visible"}])
        # The repair: the same footer after the sold-out section is visible.
        page = files["index.html"].decode()
        footer = page[page.index('      <footer class="site-footer">'):page.index("</footer>") + len("</footer>\n")]
        repaired = page.replace(footer, "").replace("  </div>\n\n  <script>", footer + "  </div>\n\n  <script>")
        self.assertNotEqual(repaired, page)
        again = self.check(None, R087_PLAN, {"index.html": repaired.encode()}, [R087_FOOTER, R087_TITLE])
        self.assertEqual(again["status"], "passed", again)
        self.assertEqual([t["status"] for t in again["requestedText"]["texts"]], ["visible", "visible"], again)

    def test_palette_load_errors_are_not_step_evidence(self):
        html = ('<p id="item" style="background:#4caf50;height:600px">visible</p>'
                '<script>sessionStorage.getItem("seen")</script>')
        result = self.check(html, [step("assert-visible", "#item")])
        self.assertEqual(result["pageErrors"]["count"], 1, "the palette load is not recorded")
        self.assertIn("green", palette_names(result))

@unittest.skipUnless(
    os.environ.get("ODS_INSPECTION_TEST_IMAGE"), "isolated Docker image test opt in"
)
class DockerCapsuleTests(unittest.TestCase):
    def invoke(self, html, steps, cancel=False, files=None, texts=None):
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
            request = bundle(html, steps, files, texts)["request"]
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

    def test_real_capsule_round087_requested_text_replay(self):
        files = {"index.html": (TOWER1_R087 / "index.html").read_bytes()}
        result = self.invoke(None, R087_PLAN, files=files, texts=[R087_FOOTER, R087_TITLE])
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["requestedText"]["texts"][0]["reason"], "display-none", result)
        self.assertEqual(result["requestedText"]["texts"][1]["status"], "visible", result)

if __name__ == "__main__":
    unittest.main()
