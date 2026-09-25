"""Real isolated-browser document capability tests; no model or live runtime."""

import copy
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/pixel-agent/host"))
from preview_inspection_document import InspectionBrowser
from preview_inspection_protocol import Invalid
from preview_inspection_capsule import run_browser, OBSERVE_ELEMENT
from test_preview_inspection import bundle

HTML = """<button onclick="document.querySelector('#item').hidden=false">Reveal</button>
<div id="item" hidden>Target</div>"""


def locator(snapshot, name):
    nodes = [node for node in snapshot["elements"] if node["name"] == name]
    if len(nodes) != 1:
        raise AssertionError(nodes)
    return {"ref": nodes[0]["ref"], "documentGeneration": snapshot["documentGeneration"]}


@unittest.skipUnless(os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1", "real Chromium opt in")
class RealDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global sync_playwright
        from playwright.sync_api import sync_playwright

    def test_reference_transition_and_css_share_exact_document(self):
        data = bundle(HTML)
        def driver(refs, execute):
            snapshot = refs.snapshot()
            target, button = locator(snapshot, "Target"), locator(snapshot, "Reveal")
            request = copy.deepcopy(data["request"])
            request["steps"] = [{"action": action, "locator": loc} for action, loc in
                [("assert-hidden", target), ("click", button), ("assert-visible", target)]]
            self.assertEqual(execute(request)["status"], "passed")
            self.assertEqual(execute(data["request"])["status"], "passed")
            return snapshot
        snapshot = run_browser(data, document_driver=driver)
        self.assertTrue(snapshot["descriptionsAreUntrusted"])

    def test_byte_identical_replacement_does_not_inherit_reference(self):
        def driver(refs, execute):
            target = locator(refs.snapshot(), "Target")
            refs.call(refs.document, "function(){const e=document.querySelector('#item');e.outerHTML=e.outerHTML}")
            with self.assertRaisesRegex(Invalid, "detached"):
                refs.observe(target["ref"], target["documentGeneration"], OBSERVE_ELEMENT)
        run_browser(bundle(HTML), document_driver=driver)

    def test_forged_generation_and_other_reference_reject(self):
        def driver(refs, execute):
            target = locator(refs.snapshot(), "Target")
            for ref, generation in [(target["ref"], "a" * 32), ("b" * 32, target["documentGeneration"])]:
                with self.assertRaisesRegex(Invalid, "unknown"):
                    refs.observe(ref, generation, OBSERVE_ELEMENT)
            with self.assertRaisesRegex(Invalid, "already issued"):
                refs.snapshot()
        run_browser(bundle(HTML), document_driver=driver)

    def test_expiry_and_close_reject(self):
        def driver(refs, execute):
            target = locator(refs.snapshot(), "Target")
            refs.deadline = refs.clock() - 1
            with self.assertRaisesRegex(Invalid, "expired"):
                refs.resolve(target["ref"], target["documentGeneration"])
            refs.close()
            self.assertEqual(refs.nodes, {})
        run_browser(bundle(HTML), document_driver=driver)

    def test_document_replacement_invalidates_generation(self):
        def driver(refs, execute):
            target = locator(refs.snapshot(), "Target")
            refs.cdp.send("Page.reload", {})
            with self.assertRaises(Invalid):
                refs.resolve(target["ref"], target["documentGeneration"])
        run_browser(bundle(HTML), document_driver=driver)

    def test_occluded_reference_does_not_report_click_success(self):
        html = HTML + '<div style="position:fixed;inset:0;background:white;z-index:999"></div>'
        def driver(refs, execute):
            button = locator(refs.snapshot(), "Reveal")
            with self.assertRaisesRegex(Invalid, "actionable"):
                refs.click(button["ref"], button["documentGeneration"], {"width":375,"height":812})
        run_browser(bundle(html), document_driver=driver)

    def test_warm_browser_new_context_has_no_previous_dom_or_refs(self):
        generations = []
        def driver(refs, execute):
            snap = refs.snapshot()
            target = locator(snap, "Target")
            self.assertFalse(refs.observe(target["ref"], target["documentGeneration"], OBSERVE_ELEMENT)["visible"])
            for prior in generations:
                with self.assertRaises(Invalid):
                    refs.resolve(prior["ref"], prior["documentGeneration"])
            generations.append(target)
            refs.click(**{"reference": locator(snap,"Reveal")["ref"], "generation":snap["documentGeneration"], "viewport":{"width":375,"height":812}})
        with InspectionBrowser(sync_playwright, calls=2) as browser:
            for _ in range(2):
                browser.inspect(bundle(HTML), document_driver=driver)
                self.assertEqual(len(browser.browser.contexts), 0)
            with self.assertRaisesRegex(Invalid, "expired"):
                browser.inspect(bundle(HTML))
        self.assertNotEqual(generations[0], generations[1])

    def test_driver_exception_closes_fresh_context(self):
        with InspectionBrowser(sync_playwright) as browser:
            def fail(*_):
                raise RuntimeError("owned fixture failure")
            with self.assertRaisesRegex(RuntimeError, "owned fixture"):
                browser.inspect(bundle(HTML), document_driver=fail)
            self.assertEqual(len(browser.browser.contexts), 0)


if __name__ == "__main__":
    unittest.main()
