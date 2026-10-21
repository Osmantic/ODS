"""One bounded viewport screenshot beside the receipt, only when directed."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import unittest
import uuid
from unittest.mock import patch

import test_preview_inspection as harness
from test_preview_inspection_blocked import execute
from test_preview_inspection_paint import SCENE, painted
from test_preview_inspection_press import PressBrowser, press

bundle, protocol, broker, capsule = harness.bundle, harness.protocol, harness.broker, harness.capsule
WALLPAPERS = Path(__file__).resolve().parents[1] / "extensions/services/dashboard/src/assets/wallpapers"
DIRECTIVE = {"maxSide": 1200, "maxPixels": 1024 * 1024, "maxBytes": 128 * 1024}


def segment(marker, payload):
    return b"\xff" + bytes([marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def jpeg(width, height, *, progressive=False, fill=False, body=b"\x00" * 32):
    """A JPEG-shaped byte string whose frame header names width x height."""
    frame = bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    return (b"\xff\xd8" + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + segment(0xDB, bytes(65))
            + (b"\xff" if fill else b"") + segment(0xC2 if progressive else 0xC0, frame) + segment(0xC4, bytes(29))
            + segment(0xDA, bytes([3, 1, 0, 2, 0x11, 3, 0x11, 0, 0x3F, 0])) + body + b"\xff\xd9")


def captured(image):
    return {"status": "captured", "mimeType": "image/jpeg", "width": protocol.jpeg_size(image)[0],
            "height": protocol.jpeg_size(image)[1], "bytes": len(image), "sha256": hashlib.sha256(image).hexdigest(),
            "data": base64.b64encode(image).decode()}


def directed(html, steps, directive=DIRECTIVE, viewport=None):
    data = bundle(html, steps, viewport=viewport)
    data["request"]["screenshot"] = dict(directive)
    return data


def config():
    return {"ownerUid": os.getuid(), "docker": "/usr/bin/docker", "transport": "local",
            "snapshotRoot": "/unused", "imageId": "sha256:" + "a" * 64}


class ScreenshotProtocol(unittest.TestCase):
    def test_the_directive_sits_beside_the_plan_and_never_changes_its_binding(self):
        plain = bundle("<canvas id=game>", [painted()])["request"]
        request = {**plain, "screenshot": dict(DIRECTIVE)}
        self.assertIs(protocol.validate_request(request), request)
        self.assertEqual(protocol.plan_request(request), plain)
        self.assertEqual(protocol.plan_hash(request), protocol.plan_hash(plain))
        self.assertEqual(protocol.inspection_scope(request), protocol.inspection_scope(plain))
        self.assertEqual(protocol.failure("unavailable", request), protocol.failure("unavailable", plain))

    def test_the_directive_is_exact_and_bounded(self):
        plain = bundle("<canvas id=game>", [painted()])["request"]
        for bad in ({}, {**DIRECTIVE, "format": "png"}, {"maxSide": 1200, "maxPixels": 1}, {**DIRECTIVE, "maxSide": 1201},
                    {**DIRECTIVE, "maxSide": 63}, {**DIRECTIVE, "maxPixels": 1024 * 1024 + 1}, {**DIRECTIVE, "maxPixels": 4095},
                    {**DIRECTIVE, "maxBytes": 128 * 1024 + 1}, {**DIRECTIVE, "maxBytes": 16 * 1024 - 1},
                    {**DIRECTIVE, "maxSide": 1200.0}, {**DIRECTIVE, "maxSide": True}, None, [1200]):
            with self.subTest(directive=bad), self.assertRaises(protocol.Invalid):
                protocol.validate_request({**plain, "screenshot": bad})

    def test_the_request_frame_holds_the_largest_directive_beside_a_plan_at_its_limit(self):
        # canonical() of the directive alone is longer than what it adds to a request.
        largest = protocol.canonical({"screenshot": {"maxBytes": 131072, "maxPixels": 1048576, "maxSide": 1200}})
        self.assertLessEqual(protocol.MAX_REQUEST + len(largest) + 1, protocol.MAX_REQUEST_FRAME, "+1: the newline")
        # The plan alone keeps its own limit.
        text = {"action": "assert-text", "locator": {"selector": "h1"}, "expectedText": "\U0001d11e" * 256}
        oversized = bundle("<h1>x</h1>", [text] * 12)["request"]
        with self.assertRaises(protocol.Invalid):
            protocol.validate_request({**oversized, "screenshot": dict(DIRECTIVE)})

    def test_the_result_bound_is_the_receipt_plus_one_base64_jpeg(self):
        self.assertEqual(protocol.MAX_SCREENSHOT_RESULT,
                         protocol.MAX_RESULT + 4 * math.ceil(protocol.MAX_SCREENSHOT_BYTES / 3) + 1024)
        self.assertEqual((protocol.MAX_SCREENSHOT_SIDE, protocol.MAX_SCREENSHOT_PIXELS), (1200, 1024 * 1024))


class JpegSize(unittest.TestCase):
    def test_real_jpegs_report_the_size_an_independent_decoder_reads(self):
        # Sizes read with Windows System.Drawing; the encoders differ per file.
        for name, size in (("sonoma-clouds.jpg", (1600, 900)), ("mountaintrees.jpg", (2400, 1139)),
                           ("midnight-8.jpg", (1600, 1600)), ("bannalpsee.jpg", (3840, 2160))):
            with self.subTest(name=name):
                self.assertEqual(protocol.jpeg_size((WALLPAPERS / name).read_bytes()), size)

    def test_baseline_progressive_and_fill_bytes(self):
        self.assertEqual(protocol.jpeg_size(jpeg(800, 600)), (800, 600))
        self.assertEqual(protocol.jpeg_size(jpeg(1200, 675, progressive=True)), (1200, 675))
        self.assertEqual(protocol.jpeg_size(jpeg(1, 65535, fill=True)), (1, 65535))

    def test_anything_else_is_refused(self):
        good = jpeg(10, 10)
        for bad in (b"", b"\xff\xd8\xff\xd9", good[:-2], b"\x89PNG" + good[4:], good[:2] + segment(0xDA, bytes(10)) + b"\xff\xd9",
                    jpeg(0, 10), jpeg(10, 0), good[:2] + b"\x00" + good[2:], "text"):
            with self.subTest(bad=bad[:12]), self.assertRaises(protocol.Invalid):
                protocol.jpeg_size(bad)


class ScreenshotMember(unittest.TestCase):
    def test_one_captured_jpeg_or_one_omission_reason(self):
        value = captured(jpeg(800, 600))
        self.assertIs(protocol.validate_screenshot(value, DIRECTIVE), value)
        for reason in protocol.SCREENSHOT_OMISSIONS:
            omitted = {"status": "omitted", "reason": reason}
            self.assertIs(protocol.validate_screenshot(omitted, DIRECTIVE), omitted)

    def test_forged_or_unbounded_members_are_refused(self):
        image = jpeg(800, 600)
        good = captured(image)
        other = jpeg(801, 600)
        for change in ({"status": "omitted"}, {"mimeType": "image/png"}, {"width": 801}, {"height": 601},
                       {"bytes": len(image) + 1}, {"sha256": "0" * 64}, {"sha256": good["sha256"].upper()},
                       {"data": base64.b64encode(other).decode()}, {"data": good["data"][:-4] + "AAAA"},
                       {"data": good["data"].replace("A", "-", 1)}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(protocol.Invalid):
                protocol.validate_screenshot({**good, **change}, DIRECTIVE)
        for directive in ({**DIRECTIVE, "maxSide": 799}, {**DIRECTIVE, "maxPixels": 800 * 600 - 1},
                          {**DIRECTIVE, "maxBytes": len(image) - 1}):
            with self.subTest(directive=directive), self.assertRaises(protocol.Invalid):
                protocol.validate_screenshot(good, directive)
        for omitted in ({"status": "omitted", "reason": "incomplete"}, {"status": "captured", "reason": "too_large"},
                        {"status": "omitted", "reason": "too_large", "detail": "x"}):
            with self.subTest(omitted=omitted), self.assertRaises(protocol.Invalid):
                protocol.validate_screenshot(omitted, DIRECTIVE)
        # A frame header that disagrees with the declared size.
        lying = captured(image)
        lying.update(width=640, height=480)
        with self.assertRaises(protocol.Invalid):
            protocol.validate_screenshot(lying, DIRECTIVE)


class ScreenshotScale(unittest.TestCase):
    def test_a_viewport_within_bounds_is_captured_at_full_size(self):
        self.assertEqual(capsule.screenshot_scale(800, 600, 1200, 1048576), 1)
        self.assertEqual(capsule.screenshot_scale(1024, 1024, 1200, 1048576), 1)
        self.assertEqual(capsule.screenshot_scale(240, 1920, 1920, 1048576), 1)

    def test_rounded_up_sides_and_area_stay_in_bounds_and_close_to_them(self):
        for directive in ((1200, 1048576), (800, 640000), (1200, 500000), (64, 4096), (1000, 1000000)):
            for width in range(240, 1921, 53):
                for height in range(240, 1921, 61):
                    with self.subTest(directive=directive, viewport=(width, height)):
                        side, pixels = directive
                        scale = capsule.screenshot_scale(width, height, side, pixels)
                        out = (math.ceil(width * scale), math.ceil(height * scale))
                        self.assertLessEqual(max(out), side)
                        self.assertLessEqual(out[0] * out[1], pixels)
                        best = min(1, side / max(width, height), math.sqrt(pixels / (width * height)))
                        self.assertGreater(scale, 0.95 * best)


VISIBLE = [harness.step("assert-visible", "#game")]


class ScreenshotBroker(unittest.TestCase):
    """The label decides whether the capsule is directed; the plan always runs."""

    def run_broker(self, request, labels, output):
        calls = []

        def process(argv, body, **kwargs):
            calls.append((argv, body, kwargs))
            if "inspect" in argv:
                return labels.pop(0)
            return output(body) if "run" in argv else b""

        with patch.object(broker, "bounded_process", side_effect=process), \
                patch.object(broker, "snapshot_bundle", side_effect=lambda _root, plan, _uid: {**bundle_for(plan), "request": plan}) as snapshot:
            result = broker.inspect_request(request, config())
        return result, calls, snapshot

    def receipt(self, request, **extra):
        return protocol.canonical({"schemaVersion": 1, "kind": protocol.KIND, "status": "passed", "siteId": request["siteId"],
                                   "sha256": request["sha256"], "planSha256": protocol.plan_hash(request),
                                   "scope": protocol.inspection_scope(request), "steps": [], **extra})

    def test_a_labelled_image_is_directed_and_its_screenshot_validated(self):
        data = directed("<canvas id=game>", VISIBLE)
        request, shot = data["request"], captured(jpeg(375, 812))
        result, calls, snapshot = self.run_broker(request, [b"viewport-screenshot-v1\n"],
                                                  lambda _body: self.receipt(request, screenshot=shot))
        self.assertEqual(result["screenshot"], shot)
        self.assertIn('org.osmantic.ods.inspection.screenshot', calls[0][0][-2])
        # The snapshot binds the plan alone; only the capsule sees the directive.
        self.assertEqual(snapshot.call_args.args[1], protocol.plan_request(request))
        capsule_bundle = json.loads(calls[1][1])
        self.assertEqual(capsule_bundle["request"]["screenshot"], DIRECTIVE)
        self.assertEqual(calls[1][2]["limit"], protocol.MAX_SCREENSHOT_RESULT)

    def test_an_older_image_runs_the_plan_and_omits_only_the_screenshot(self):
        request = directed("<canvas id=game>", VISIBLE)["request"]
        for label in (b"", b"<no value>\n", b"viewport-screenshot-v0\n"):
            with self.subTest(label=label):
                result, calls, _ = self.run_broker(request, [label], lambda _body: self.receipt(request))
                self.assertEqual(result["screenshot"], {"status": "omitted", "reason": "capability_missing"})
                capsule_bundle = json.loads(calls[1][1])
                self.assertNotIn("screenshot", capsule_bundle["request"])
                self.assertEqual(calls[1][2]["limit"], protocol.MAX_RESULT)

    def test_an_undirected_request_reads_no_label_and_keeps_the_old_limit(self):
        request = bundle("<canvas id=game>", [harness.step("assert-visible", "#game")])["request"]
        result, calls, _ = self.run_broker(request, [], lambda _body: self.receipt(request))
        self.assertNotIn("screenshot", result)
        self.assertEqual(len(calls), 2, "the capsule run and its cleanup only")
        self.assertEqual(calls[0][2]["limit"], protocol.MAX_RESULT)

    def test_capsule_output_must_answer_the_directive_exactly(self):
        directed_request = directed("<canvas id=game>", VISIBLE)["request"]
        plain = bundle("<canvas id=game>", VISIBLE)["request"]
        shot = captured(jpeg(375, 812))
        for request, labels, output in (
            (directed_request, [b"viewport-screenshot-v1\n"], self.receipt(directed_request)),
            (plain, [], self.receipt(plain, screenshot=shot)),
            (directed_request, [b"viewport-screenshot-v1\n"], self.receipt(directed_request, screenshot={**shot, "width": 376})),
            (directed_request, [b"viewport-screenshot-v1\n"], self.receipt(directed_request, screenshot={"status": "omitted", "reason": "x"})),
            (directed_request, [b"viewport-screenshot-v1\n"], protocol.canonical({**protocol.failure("timeout", directed_request), "screenshot": shot})),
        ):
            with self.subTest(output=output[:80]), self.assertRaises(protocol.Invalid):
                self.run_broker(request, list(labels), lambda _body, output=output: output)

    def test_a_failure_receipt_carries_no_screenshot(self):
        request = directed("<canvas id=game>", VISIBLE)["request"]
        failure = protocol.failure("request_blocked", request)
        result, _, _ = self.run_broker(request, [b"viewport-screenshot-v1\n"], lambda _body: protocol.canonical(failure))
        self.assertEqual(result, failure)

    def test_the_export_helper_never_sees_the_directive(self):
        request = directed("<canvas id=game>", VISIBLE)["request"]
        plan = protocol.plan_request(request)
        exported = []

        def process(argv, body, **_):
            if "export" in argv:
                exported.append(json.loads(body))
                return protocol.canonical(bundle_for(plan))
            if "inspect" in argv:
                return b"viewport-screenshot-v1\n"
            if "run" in argv:
                self.assertEqual(json.loads(body)["request"]["screenshot"], DIRECTIVE)
                return self.receipt(request, screenshot=captured(jpeg(375, 812)))
            return b""

        native = {**config(), "transport": "docker-desktop", "dockerSocket": "/home/owner/.docker/run/docker.sock"}
        with patch.object(broker, "bounded_process", side_effect=process):
            result = broker.inspect_request(request, native)
        self.assertEqual(exported, [plan])
        self.assertEqual(result["screenshot"]["status"], "captured")


def bundle_for(request):
    data = bundle("<canvas id=game>", request["steps"], viewport=request["viewport"])
    return {**data, "request": request}


class ScreenshotBrowser(PressBrowser):
    """PressBrowser that also answers the viewport JPEG capture: `shot(params)`
    returns its bytes (by default a JPEG of the scaled clip size), `error` makes
    it raise, `closed` closes the page before the capture."""

    def __init__(self, shot=None, error=None, closed=False, **kwargs):
        super().__init__(**kwargs)
        self.shot, self.error, self.closed, self.shots = shot, error, closed, []

    def is_closed(self):
        return self.closed

    def send(self, method, params=None):
        if method == "Page.captureScreenshot" and (params or {}).get("format") == "jpeg":
            self.shots.append(params)
            self.calls.append("screenshot:%d" % params["quality"])
            if self.error:
                raise self.error
            clip = params["clip"]
            image = self.shot(params) if self.shot else jpeg(math.ceil(clip["width"] * clip["scale"]),
                                                             math.ceil(clip["height"] * clip["scale"]))
            return {"data": base64.b64encode(image).decode()}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(SCENE).decode()}
        return super().send(method, params)


class Clock:
    """The capsule's clock: the first reading starts the run, later ones repeat the last value."""

    def __init__(self, values):
        self.values = list(values)

    def monotonic(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


GAME = "<canvas id=game></canvas><p id=item hidden></p>"
PLAN = [harness.step("assert-hidden", "#item"), press(), harness.step("assert-visible", "#item")]


class ScriptedScreenshot(unittest.TestCase):
    def run_capsule(self, browser, data):
        browser.site = data["request"]["siteId"]
        return capsule.run_browser(data, playwright_factory=browser)

    def test_one_capture_of_the_whole_viewport_after_the_key_press(self):
        browser = ScreenshotBrowser()
        result = self.run_capsule(browser, directed(GAME, PLAN))
        self.assertEqual(result["status"], "passed", result)
        actions = [c for c in browser.calls if c.startswith(("press:", "screenshot:", "close"))]
        self.assertEqual(actions[:3], ["press:Space:100", "screenshot:80", "close"], "after the press, before the context closes")
        self.assertEqual(browser.shots, [{"format": "jpeg", "quality": 80, "captureBeyondViewport": False,
                                          "clip": {"x": 0, "y": 0, "width": 375, "height": 812, "scale": 1}}])
        self.assertEqual(result["screenshot"], captured(jpeg(375, 812)))
        # The receipt itself is the one an undirected run produces.
        plain = self.run_capsule(ScreenshotBrowser(), bundle(GAME, PLAN))
        self.assertNotIn("screenshot", plain)
        self.assertEqual({key: value for key, value in result.items() if key != "screenshot"}, plain)

    def test_a_failed_step_still_shows_the_state_it_left(self):
        browser = ScreenshotBrowser()
        result = self.run_capsule(browser, directed(GAME, [press(), harness.step("assert-hidden", "#item"),
                                                           harness.step("assert-visible", "#game")]))
        self.assertEqual((result["status"], len(result["steps"])), ("failed", 2))
        self.assertEqual(result["screenshot"]["status"], "captured")

    def test_a_large_viewport_is_downscaled_within_the_directive(self):
        browser = ScreenshotBrowser()
        result = self.run_capsule(browser, directed(GAME, [harness.step("assert-visible", "#game")],
                                                    viewport={"width": 1920, "height": 1080}))
        clip = browser.shots[0]["clip"]
        self.assertEqual((clip["width"], clip["height"]), (1920, 1080))
        self.assertLess(clip["scale"], 1)
        shot = result["screenshot"]
        self.assertLessEqual(max(shot["width"], shot["height"]), 1200)
        self.assertLessEqual(shot["width"] * shot["height"], 1024 * 1024)
        self.assertGreater(shot["width"], 1150)

    def test_a_smaller_model_limit_is_respected(self):
        browser = ScreenshotBrowser()
        result = self.run_capsule(browser, directed(GAME, [harness.step("assert-visible", "#game")],
                                                    {"maxSide": 512, "maxPixels": 200000, "maxBytes": 16384}))
        shot = result["screenshot"]
        self.assertLessEqual(max(shot["width"], shot["height"]), 512)
        self.assertLessEqual(shot["width"] * shot["height"], 200000)

    def test_a_capture_over_the_byte_limit_retries_once_at_lower_quality(self):
        big, small = jpeg(375, 812, body=bytes(20000)), jpeg(375, 812)
        browser = ScreenshotBrowser(shot=lambda params: big if params["quality"] == 80 else small)
        directive = {**DIRECTIVE, "maxBytes": 16384}
        result = self.run_capsule(browser, directed(GAME, PLAN, directive))
        self.assertEqual([s["quality"] for s in browser.shots], [80, 50])
        self.assertEqual(result["screenshot"], captured(small))
        browser = ScreenshotBrowser(shot=lambda _params: big)
        result = self.run_capsule(browser, directed(GAME, PLAN, directive))
        self.assertEqual(result["screenshot"], {"status": "omitted", "reason": "too_large"})
        self.assertEqual(result["status"], "passed")

    def test_failed_or_unexpected_captures_are_omitted_never_the_receipt(self):
        for name, browser in (("error", ScreenshotBrowser(error=capsule.BrowserError("Target closed"))),
                              ("not-jpeg", ScreenshotBrowser(shot=lambda _p: SCENE)),
                              ("too-wide", ScreenshotBrowser(shot=lambda _p: jpeg(1201, 10)))):
            with self.subTest(name=name):
                result = self.run_capsule(browser, directed(GAME, PLAN))
                self.assertEqual(result["status"], "passed")
                self.assertEqual(result["screenshot"], {"status": "omitted", "reason": "capture_failed"})

    def test_a_closed_page_or_a_spent_time_budget_is_never_captured(self):
        browser = ScreenshotBrowser(closed=True)
        result = self.run_capsule(browser, directed(GAME, PLAN))
        self.assertEqual(result["screenshot"], {"status": "omitted", "reason": "page_unavailable"})
        self.assertEqual(browser.shots, [])
        browser = ScreenshotBrowser()
        with patch.object(capsule, "time", Clock([0.0, capsule.SCREENSHOT_START_BUDGET_S])):
            result = self.run_capsule(browser, directed(GAME, PLAN))
        self.assertEqual(result["screenshot"], {"status": "omitted", "reason": "time_budget"})
        self.assertEqual(browser.shots, [])

    def test_an_undirected_run_never_captures(self):
        browser = ScreenshotBrowser()
        result = self.run_capsule(browser, bundle(GAME, PLAN))
        self.assertEqual(browser.shots, [])
        self.assertNotIn("screenshot", result)

    def test_the_receipt_keeps_its_own_limit_beside_the_screenshot(self):
        shot = captured(jpeg(375, 812, body=bytes(100000)))
        data = directed(GAME, PLAN)
        receipt = {"schemaVersion": 1, "kind": protocol.KIND, "status": "passed", "screenshot": shot}
        with patch.object(capsule, "run_browser", return_value=dict(receipt)):
            output = execute(data)
        self.assertEqual(output["screenshot"], shot)
        too_large = {**receipt, "padding": "x" * protocol.MAX_RESULT}
        with patch.object(capsule, "run_browser", return_value=too_large):
            output = execute(data)
        self.assertEqual(output["errorCode"], "output_limit")
        self.assertNotIn("screenshot", output)


def execute_real(payload):
    """The capsule in its image (or in-process with the opt-in fixture browser),
    with the screenshot result bound instead of the receipt-only one."""
    image = os.environ.get("ODS_INSPECTION_TEST_IMAGE")
    if not image:
        return execute(payload)
    if not __import__("re").fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("test image must be an exact local image ID")
    docker = {"docker": "/usr/bin/docker", "transport": "local", "imageId": image}
    name = "ods-inspection-screenshot-test-" + uuid.uuid4().hex
    try:
        result = subprocess.run(broker.capsule_argv(docker, name), input=protocol.canonical(payload),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=True)
        if len(result.stdout) > protocol.MAX_SCREENSHOT_RESULT:
            raise ValueError("test output exceeded protocol bound")
        return json.loads(result.stdout)
    finally:
        subprocess.run([*broker.docker_prefix(docker), "rm", "-f", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


@unittest.skipUnless(os.environ.get("ODS_PREVIEW_BROWSER_TESTS") == "1" or os.environ.get("ODS_INSPECTION_TEST_IMAGE"),
                     "requires isolated real browser")
class RealScreenshot(unittest.TestCase):
    BLANK = '<body style="background:#000"><canvas id="game" width="400" height="300" style="background:#000"></canvas></body>'
    SPRITE = '''<body style="background:#000"><canvas id="game" width="400" height="300" style="background:#000"></canvas><script>
    const context = document.querySelector('#game').getContext('2d');
    let x = 20;
    function draw() { context.clearRect(0, 0, 400, 300); context.fillStyle = '#e53935'; context.fillRect(x, 120, 60, 60); }
    draw();
    window.addEventListener('keydown', event => { if (event.key === 'ArrowRight') { x += 120; draw(); } });
    </script></body>'''
    VIEWPORT = {"width": 800, "height": 600}

    def shot(self, html, steps, viewport=None):
        result = execute_real(directed(html, steps, viewport=viewport or self.VIEWPORT))
        screenshot = result["screenshot"]
        self.assertEqual(screenshot["status"], "captured", result)
        protocol.validate_screenshot(screenshot, DIRECTIVE)
        return result, screenshot

    # Paired plans differ only in the key: both focus the same frame and canvas,
    # so any difference between their JPEGs is what the key changed on screen.
    def keys(self, key):
        return [press(key=key), press(key=key), painted()]

    def test_a_blank_canvas_is_still_captured_and_no_key_changes_it(self):
        left, unhandled = self.shot(self.BLANK, self.keys("ArrowLeft"))
        right, handled = self.shot(self.BLANK, self.keys("ArrowRight"))
        for result in (left, right):
            self.assertEqual(result["steps"][2]["errorCode"], "uniform_paint", result)
        self.assertEqual((handled["width"], handled["height"]), (800, 600))
        self.assertEqual(handled["sha256"], unhandled["sha256"], "a blank canvas looks the same after any key")

    def test_a_drawn_canvas_is_captured_as_the_presses_left_it(self):
        still_result, still = self.shot(self.SPRITE, self.keys("ArrowLeft"))
        moved_result, moved = self.shot(self.SPRITE, self.keys("ArrowRight"))
        for result in (still_result, moved_result):
            self.assertEqual(result["status"], "passed", result)
        self.assertNotEqual(moved["sha256"], still["sha256"], "the sprite moved before the capture")
        _, again = self.shot(self.SPRITE, self.keys("ArrowRight"))
        self.assertEqual(again["sha256"], moved["sha256"], "the same state captures the same JPEG")
        _, blank = self.shot(self.BLANK, self.keys("ArrowRight"))
        self.assertNotEqual(blank["sha256"], moved["sha256"])

    def test_a_wide_viewport_is_downscaled_within_bounds(self):
        _, wide = self.shot(self.SPRITE, [painted()], viewport={"width": 1920, "height": 1080})
        self.assertLessEqual(max(wide["width"], wide["height"]), 1200)
        self.assertLessEqual(wide["width"] * wide["height"], 1024 * 1024)
        self.assertGreater(wide["width"], 1150)

    def test_an_undirected_real_run_has_no_screenshot(self):
        result = execute_real(bundle(self.SPRITE, [painted()], viewport=self.VIEWPORT))
        self.assertEqual(result["status"], "passed", result)
        self.assertNotIn("screenshot", result)


if __name__ == "__main__":
    unittest.main()
