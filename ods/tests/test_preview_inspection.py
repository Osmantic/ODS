"""Boundaries and opt-in real browser regressions (ODS_PREVIEW_BROWSER_TESTS=1)."""

import base64
import copy
import hashlib
import os
import socket
import sys
import tempfile
import time
import unittest
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


def bundle(html, steps=None):
    data = html.encode()
    name = b"index.html"
    digest = hashlib.sha256(
        len(name).to_bytes(4, "big") + name + len(data).to_bytes(8, "big") + data
    ).hexdigest()
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
        "files": [{"path": "index.html", "base64": base64.b64encode(data).decode()}],
    }


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

    def __init__(self, load_errors=(), click_errors=()):
        self.load_errors, self.click_errors = list(load_errors), list(click_errors)
        self.handlers, self.calls, self.revealed = {}, [], False

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

    def new_context(self, **_):
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
        pass


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


@unittest.skipUnless(
    os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1", "real Chromium opt in"
)
class BrowserTests(unittest.TestCase):
    def check(self, html, steps):
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
                protocol.canonical(bundle(html, steps)), timeout=20
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


@unittest.skipUnless(
    os.environ.get("ODS_INSPECTION_TEST_IMAGE"), "isolated Docker image test opt in"
)
class DockerCapsuleTests(unittest.TestCase):
    def invoke(self, html, steps, cancel=False):
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
            (site / "index.html").write_text(html)
            (site / "index.html").chmod(0o600)
            previews = root / "previews"
            previews.mkdir(mode=0o700)
            receipt = publisher.publish_snapshot(
                workspace, previews, "site", os.getuid()
            )
            request = bundle(html, steps)["request"]
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


if __name__ == "__main__":
    unittest.main()
