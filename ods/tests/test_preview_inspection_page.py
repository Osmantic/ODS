"""A page of the same snapshot to start on: no new permission, every receipt names it."""
import os
import re
import unittest
import urllib.request
from unittest.mock import patch

import test_preview_inspection as harness
from test_preview_inspection_blocked import execute_real

bundle, protocol, broker, capsule = harness.bundle, harness.protocol, harness.broker, harness.capsule
step, Route, PaletteDouble, ScriptedBrowser = harness.step, harness.Route, harness.PaletteDouble, harness.ScriptedBrowser

# A three-page bakery site with nested pages, as an owner asks for one.
SITE = {
    "index.html": b'<h1>Crumb &amp; Co</h1><a id="to-menu" href="menu.html">Menu</a> <a href="contact.html">Contact</a>',
    "menu.html": b'<h1>Menu</h1><p id="bread">Sourdough</p><a id="to-contact" href="contact.html">Contact</a>',
    "contact.html": b'<h1>Contact</h1><p id="phone">555 0100</p>',
    "about/index.html": b'<h1>About</h1><link rel="stylesheet" href="../style.css">',
    "about/team.html": b'<h1>Team</h1>',
    "[slug].html": b'<h1>Post</h1>',
    "style.css": b"h1{color:#7a4b2a}",
}
LONG = ("x" * 120 + "/") * 4
GOOD = ("menu.html", "about/team.html", "about/", "index.html", "[slug].html", "_not-found.html", "v1.2/notes.html",
        "a/b/c/d/e/f/g/h/i/j/k/x.html", "x/" * 11, "x" * 123 + ".html", LONG + "x" * 23 + ".html")
# Exactly the rejected pages of inspection_page.test.mjs.
BAD = ("", "/", "//", "/menu.html", "\\menu.html", "menu\\x.html", "menu\x00.html", "../menu.html", "about/../menu.html",
       "./menu.html", "about/./team.html", "about//team.html", "http://example.invalid/menu.html", "//example.invalid/menu.html",
       "file:menu.html", "menu.html?x=1", "menu.html#top", ".hidden.html", "about/.secret.html", ".well-known/", "menu",
       "menu.htm", "menu.HTML", "menu.html.bak", "style.css", "about", "about/team", "%2e%2e/menu.html", "men u.html",
       "ménu.html", "menu.html\n", "__ods_view__.html", "__pycache__/x.html", "-menu.html", "x" * 124 + ".html",
       "a/" * 12 + "x.html", "x/" * 12, LONG + "x" * 24 + ".html", None, 7, ["menu.html"])
# The plan of tests/test-preview-inspection.mjs. LEGACY_HASH is its plan_hash
# on main before page existed, and the plugin's hash of it.
LEGACY = {"schemaVersion": 1, "action": "inspect", "siteId": "site-" + "a" * 24, "sha256": "a" * 64,
          "viewport": {"width": 375, "height": 812},
          "steps": [{"action": "assert-hidden", "locator": {"selector": "#card"}},
                    {"action": "click", "locator": {"role": "button", "name": "Mostrar próximos eventos", "exact": True}},
                    {"action": "assert-visible", "locator": {"selector": "#card"}}]}
LEGACY_HASH = "156ae762e4eb8cbec17063823bfb5a61705c2917251da8cdff43af2c9ffbc06c"
DIRECTIVE = {"maxSide": 1200, "maxPixels": 1048576, "maxBytes": 131072}


def site(page=None, steps=None):
    data = bundle("", steps or [step("assert-visible", "h1")], files=SITE)
    if page is not None:
        data["request"]["page"] = page
    return data


def config(**extra):
    return {"ownerUid": os.getuid(), "docker": "/usr/bin/docker", "transport": "local",
            "snapshotRoot": "/unused", "imageId": "sha256:" + "a" * 64, **extra}


def passed(request):
    """A passed capsule receipt for this request, as the broker relays it."""
    return {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed", "siteId": request["siteId"],
            "sha256": request["sha256"], "planSha256": protocol.plan_hash(request),
            **({"page": request["page"]} if "page" in request else {}), "viewport": request["viewport"],
            "steps": [], "diagnostics": {}, "blockedRequests": [], "scope": protocol.inspection_scope(request)}


class PageProtocol(unittest.TestCase):
    def test_existing_requests_validate_and_hash_exactly_as_before(self):
        self.assertEqual(protocol.validate_request(dict(LEGACY)), LEGACY)
        self.assertEqual(protocol.plan_hash(LEGACY), LEGACY_HASH)
        self.assertEqual(protocol.plan_hash({**LEGACY, "screenshot": DIRECTIVE}), LEGACY_HASH)
        self.assertEqual(protocol.inspection_scope(LEGACY), protocol.SCOPE)
        self.assertEqual(set(protocol.failure("unavailable", LEGACY)),
                         {"schemaVersion", "kind", "status", "errorCode", "siteId", "sha256", "planSha256", "scope"})
        # A page is part of the plan; the screenshot directive still is not.
        paged = {**LEGACY, "page": "menu.html"}
        self.assertNotEqual(protocol.plan_hash(paged), LEGACY_HASH)
        self.assertEqual(protocol.plan_hash({**paged, "screenshot": DIRECTIVE}), protocol.plan_hash(paged))
        protocol.validate_request({**paged, "screenshot": DIRECTIVE})
        # One optional field more; nothing else is loosened.
        for extra in ({"pages": ["menu.html"]}, {"url": "http://localhost/menu.html"}, {"path": "menu.html"}):
            with self.subTest(extra=extra), self.assertRaises(protocol.Invalid):
                protocol.validate_request({**paged, **extra})

    def test_a_page_is_a_relative_published_html_path(self):
        for page in GOOD:
            with self.subTest(page=page):
                self.assertEqual(protocol.validate_request({**LEGACY, "page": page})["page"], page)
        self.assertEqual(protocol.page_file("about/"), "about/index.html")
        self.assertEqual(protocol.page_file("menu.html"), "menu.html")
        for page in BAD:
            with self.subTest(page=page), self.assertRaisesRegex(protocol.Invalid, "invalid page"):
                protocol.validate_request({**LEGACY, "page": page})

    def test_the_page_must_be_a_file_of_the_snapshot(self):
        for page in ("menu.html", "about/team.html", "about/", "index.html", "[slug].html"):
            with self.subTest(page=page):
                request, files = protocol.validate_bundle(site(page))
                self.assertEqual(request["page"], page)
                self.assertIn(protocol.page_file(page), files)
        for page in ("missing.html", "about/missing.html", "contact/", "Menu.html", "blog/"):
            with self.subTest(page=page), self.assertRaises(protocol.MissingPage):
                protocol.validate_bundle(site(page))
        self.assertTrue(issubclass(protocol.MissingPage, protocol.Invalid))

    def test_the_scope_and_failures_of_a_page_plan_name_the_page(self):
        request = site("menu.html")["request"]
        self.assertEqual(protocol.inspection_scope(request), protocol.SCOPE + protocol.PAGE_SCOPE)
        self.assertIn("links between pages were not followed", protocol.PAGE_SCOPE)
        self.assertNotIn(protocol.PAGE_SCOPE, protocol.inspection_scope(site()["request"]))
        failed = protocol.failure("page_not_found", request)
        self.assertEqual((failed["page"], failed["planSha256"]), ("menu.html", protocol.plan_hash(request)))


class PageBroker(unittest.TestCase):
    def test_an_image_without_the_label_is_refused_before_reading_the_snapshot(self):
        request = site("menu.html")["request"]
        for label in (b"", b"<no value>\n", b"snapshot-page-v0\n"):
            with self.subTest(label=label), patch.object(broker, "bounded_process", return_value=label) as process, \
                    patch.object(broker, "snapshot_bundle") as snapshot:
                result = broker.inspect_request(request, config())
            self.assertEqual(result, protocol.failure("unsupported_capability", request))
            self.assertEqual(result["page"], "menu.html")
            self.assertEqual(process.call_count, 1)
            self.assertIn("org.osmantic.ods.inspection.page", str(process.call_args))
            snapshot.assert_not_called()

    def test_plans_without_a_page_never_ask_for_the_label(self):
        class Reached(Exception):
            pass
        with patch.object(broker, "bounded_process") as process, \
                patch.object(broker, "snapshot_bundle", side_effect=Reached), self.assertRaises(Reached):
            broker.inspect_request(site()["request"], config())
        process.assert_not_called()

    def run_broker(self, request, settings, snapshot):
        """The broker with a page-labelled image: its result, the request the
        snapshot export saw and the bundle the capsule received."""
        seen = {}

        def process(argv, body, **_kwargs):
            if "inspect" in argv:
                return b"snapshot-page-v1\n"
            if "exec" in argv:
                seen["export"] = protocol.strict_json(body)
                return protocol.canonical(snapshot)
            if "run" in argv:
                seen["capsule"] = protocol.strict_json(body)
                return protocol.canonical(passed(seen["capsule"]["request"]))
            return b""

        def local(_root, exported, _owner):
            seen["export"] = exported
            return snapshot

        with patch.object(broker, "bounded_process", side_effect=process), \
                patch.object(broker, "snapshot_bundle", side_effect=local):
            return broker.inspect_request(request, settings), seen

    def test_the_export_never_sees_the_page_and_the_capsule_gets_it_bound(self):
        data = site()
        request = {**data["request"], "page": "menu.html"}
        native = config(transport="docker-desktop", docker="/Applications/Docker.app/Contents/Resources/bin/docker",
                        dockerSocket="/Users/owner/.docker/run/docker.sock", snapshotRoot="/previews")
        for settings in (config(), native):
            with self.subTest(transport=settings["transport"]):
                result, seen = self.run_broker(request, settings, data)
                # An older publisher-side export helper keeps working.
                self.assertEqual(seen["export"], data["request"])
                self.assertEqual(seen["capsule"], {**data, "request": request})
                self.assertEqual((result["status"], result["page"]), ("passed", "menu.html"))

    def test_a_page_outside_the_snapshot_is_refused_before_any_browser_runs(self):
        data = site()
        for page in ("missing.html", "about/missing.html", "contact/", "Menu.html"):
            request = {**data["request"], "page": page}
            with self.subTest(page=page):
                result, seen = self.run_broker(request, config(), data)
                self.assertEqual(result, protocol.failure("page_not_found", request))
                self.assertNotIn("capsule", seen)

    def test_a_receipt_must_name_exactly_the_requested_page(self):
        data = site()
        cases = [({**data["request"], "page": "menu.html"}, None), ({**data["request"], "page": "menu.html"}, "contact.html"),
                 (data["request"], "index.html")]
        for request, named in cases:
            receipt = {key: value for key, value in passed(request).items() if key != "page"}
            if named is not None:
                receipt["page"] = named
            with self.subTest(page=request.get("page"), named=named), \
                    patch.object(broker, "bounded_process", side_effect=lambda argv, *_a, receipt=receipt, **_k:
                                 b"snapshot-page-v1\n" if "inspect" in argv else protocol.canonical(receipt) if "run" in argv else b""), \
                    patch.object(broker, "snapshot_bundle", return_value=data), \
                    self.assertRaisesRegex(protocol.Invalid, "invalid capsule receipt"):
                broker.inspect_request(request, config())


class PageBrowser(ScriptedBrowser):
    """The scripted browser of test_preview_inspection.py, loading the wrapper
    the capsule's own loopback server serves: each navigation goes through the
    installed request guard and only an admitted one loads. `click_to` is a
    sibling page that a click navigates to, as a link would."""

    def __init__(self, click_to=None, **kwargs):
        super().__init__(**kwargs)
        self.click_to, self.routes, self.wrapper, self.served, self.url = click_to, [], None, None, None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def route(self, _pattern, handler):
        self.guard = handler

    def navigate(self, url):
        route = Route(url, navigation=True)
        self.guard(route)
        self.routes.append(route)
        return route.outcome == "continued"

    def goto(self, url, **_):
        self.calls.append("goto")
        self.origin = url.split("/__ods_inspection__.html")[0]
        if self.navigate(url):
            with self.opener.open(url, timeout=5) as response:
                self.wrapper = response.read().decode()
            src = self.origin + re.search(r'src="([^"]*)"', self.wrapper).group(1)
            if self.navigate(src):
                with self.opener.open(src, timeout=5) as response:
                    self.served = response.read()
                self.url = src
        self.emit(self.load_errors)

    def click(self, **_):
        if self.click_to:
            self.navigate(self.origin + "/" + self.site + "/" + self.click_to)
        super().click()


class PagePalette(PaletteDouble):
    """The palette load of a page plan, whose wrapper frames that page."""

    def __init__(self, image, page):
        super().__init__(image)
        self.page = page

    def goto(self, url, **kwargs):
        self.during_load = [("/" + self.site + "/" + self.page, True)]
        super().goto(url, **kwargs)
        self.url = self.origin + "/" + self.site + "/" + self.page


class PageCapsule(unittest.TestCase):
    PREFIX = "/site-" + "a" * 24 + "/"

    def run_scripted(self, browser, data):
        browser.site = data["request"]["siteId"]
        return capsule.run_browser(data, playwright_factory=browser)

    def test_the_wrapper_frames_the_requested_page(self):
        prefix = self.PREFIX
        for page, src in (("menu.html", prefix + "menu.html"), ("about/", prefix + "about/"),
                          ("[slug].html", prefix + "%5Bslug%5D.html"), (None, prefix)):
            entry = capsule.entry_path(prefix, {} if page is None else {"page": page})
            document = capsule.wrapper_document(entry).decode()
            with self.subTest(page=page):
                self.assertEqual(entry, src)
                self.assertIn(f'src="{src}"', document)
                self.assertNotIn("<script", document.lower())
                self.assertIn('sandbox="' + protocol.SANDBOX + '"', document)
                # The served URL is exactly the file validate_bundle bound.
                self.assertEqual(capsule.snapshot_file(entry, prefix, SITE), "index.html" if page is None else protocol.page_file(page))

    def test_the_guard_admits_the_requested_page_as_the_one_entry_navigation(self):
        origin, prefix = "http://127.0.0.1:4321", self.PREFIX

        def guarded(entry):
            context, blocked = PaletteDouble(b""), []
            capsule.guard_requests(context, context, origin, prefix, blocked, files=SITE, entry=entry)

            def go(path, navigation=True):
                route = Route(origin + path, navigation)
                context.guard(route)
                return route.outcome
            return go, blocked

        go, blocked = guarded(prefix + "menu.html")
        self.assertEqual([go("/__ods_inspection__.html"), go(prefix + "menu.html"), go(prefix + "style.css", False)],
                         ["continued", "continued", "continued"])
        # A click-through to a sibling page is one more navigation: blocked.
        self.assertEqual(go(prefix + "contact.html"), "aborted")
        self.assertEqual(blocked, ["navigation"])
        # The page replaces the site entry; no other page loads in its place.
        for path in (prefix, prefix + "index.html", prefix + "contact.html", prefix + "menu.html?x=1"):
            go, blocked = guarded(prefix + "menu.html")
            go("/__ods_inspection__.html")
            with self.subTest(path=path):
                self.assertEqual(go(path), "aborted")
                self.assertEqual(blocked, ["navigation"])
        # Without a page the site entry stays the one entry navigation.
        go, _ = guarded(prefix)
        self.assertEqual([go("/__ods_inspection__.html"), go(prefix)], ["continued", "continued"])
        go, _ = guarded(prefix)
        go("/__ods_inspection__.html")
        self.assertEqual(go(prefix + "menu.html"), "aborted")

    def test_a_page_plan_loads_that_page_and_its_receipt_names_it(self):
        browser = PageBrowser()
        data = site("menu.html", [step("assert-visible", "#bread")])
        result = self.run_scripted(browser, data)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["page"], "menu.html")
        self.assertEqual(result["planSha256"], protocol.plan_hash(data["request"]))
        self.assertEqual(result["scope"], protocol.SCOPE + protocol.PAGE_SCOPE)
        # The loopback server's wrapper framed menu.html, and the guard admitted it.
        self.assertIn('src="/' + browser.site + '/menu.html"', browser.wrapper)
        self.assertEqual([route.outcome for route in browser.routes], ["continued", "continued"])
        self.assertEqual(browser.served, SITE["menu.html"])
        # Without a page the receipt keeps exactly its former shape.
        entry = PageBrowser()
        result = self.run_scripted(entry, site(None, [step("assert-visible", "h1")]))
        self.assertEqual(set(result), {"schemaVersion", "kind", "status", "siteId", "sha256", "planSha256", "viewport",
                                       "steps", "diagnostics", "blockedRequests", "scope"})
        self.assertEqual(result["scope"], protocol.SCOPE)
        self.assertIn('src="/' + entry.site + '/"', entry.wrapper)
        self.assertEqual(entry.served, SITE["index.html"])

    def test_a_click_through_to_a_sibling_page_is_blocked(self):
        browser = PageBrowser(click_to="contact.html")
        result = self.run_scripted(browser, site("menu.html", [step("click", "#to-contact"), step("assert-visible", "h1")]))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["blockedRequests"], ["navigation"])
        self.assertEqual([(item["action"], item["errorCode"]) for item in result["steps"]], [("click", "click_failed")])
        self.assertEqual(result["page"], "menu.html")
        self.assertEqual([route.outcome for route in browser.routes], ["continued", "continued", "aborted"])

    def test_the_palette_is_captured_from_the_same_page(self):
        palette = PagePalette(harness.PaletteCaptureTests.GREEN, "menu.html")
        result = self.run_scripted(PageBrowser(palette=palette), site("menu.html"))
        self.assertEqual(result["status"], "passed", result)
        self.assertIn("renderedColors", result)
        self.assertEqual([route.outcome for route in palette.routes], ["continued", "continued"])


@unittest.skipUnless(os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1" or os.environ.get("ODS_INSPECTION_TEST_IMAGE"),
                     "requires isolated real browser")
class RealPages(unittest.TestCase):
    def text(self, selector, value):
        return {"action": "assert-text", "locator": {"selector": selector}, "expectedText": value}

    def test_a_three_page_site_is_checked_page_by_page(self):
        for page, selector, value in (("menu.html", "#bread", "Sourdough"), ("contact.html", "#phone", "555 0100"),
                                      ("about/", "h1", "About"), ("about/team.html", "h1", "Team"), ("[slug].html", "h1", "Post")):
            with self.subTest(page=page):
                result = execute_real(site(page, [self.text(selector, value)]))
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["page"], page)
                self.assertEqual(result["blockedRequests"], [])
        entry = execute_real(site(None, [self.text("h1", "Crumb & Co")]))
        self.assertEqual(entry["status"], "passed", entry)
        self.assertNotIn("page", entry)

    def test_links_between_pages_are_not_followed(self):
        result = execute_real(site("menu.html", [step("click", "#to-contact"), self.text("h1", "Contact")]))
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("navigation", result["blockedRequests"])
        self.assertEqual(result["page"], "menu.html")


if __name__ == "__main__":
    unittest.main()
