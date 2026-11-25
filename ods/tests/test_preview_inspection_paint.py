"""Element paint on the published snapshot: a blank detector, never image bytes."""
import base64
import hashlib
import itertools
import json
import os
import unittest
from unittest.mock import patch

import test_preview_inspection as harness
from test_preview_inspection_blocked import execute_real

bundle, protocol, broker, capsule = harness.bundle, harness.protocol, harness.broker, harness.capsule
encode_png = harness.encode_png


def painted(selector='#game'):
    return {'action': 'assert-painted', 'locator': {'selector': selector}}


def text(value, selector='h1'):
    return {'action': 'assert-text', 'locator': {'selector': selector}, 'expectedText': value}


def press(key='ArrowRight', selector='#game'):
    return {'action': 'press', 'locator': {'selector': selector}, 'key': key}


def config():
    return {'ownerUid': os.getuid(), 'docker': '/usr/bin/docker', 'transport': 'local',
            'snapshotRoot': '/unused', 'imageId': 'sha256:' + 'a' * 64}


def box(left, top, right, bottom, radius_x=0, radius_y=None, viewport=(375, 812)):
    return {'left': left, 'top': top, 'right': right, 'bottom': bottom, 'radiusX': radius_x,
            'radiusY': radius_x if radius_y is None else radius_y,
            'viewportWidth': viewport[0], 'viewportHeight': viewport[1]}


BLACK = encode_png(16, 12, [(0, 0, 0)] * 192)
SCENE = encode_png(16, 12, [(0, 0, 0)] * 144 + [(229, 57, 53)] * 48)
OTHER = encode_png(16, 12, [(229, 57, 53)] * 48 + [(0, 0, 0)] * 144)


class PaintProtocol(unittest.TestCase):
    def test_assert_painted_takes_exactly_action_and_locator(self):
        protocol.validate_request(bundle('<canvas id=game>', [painted()])['request'])
        protocol.validate_request(bundle('<button>Go</button>', [
            {'action': 'assert-painted', 'locator': {'role': 'button', 'name': 'Go', 'exact': True}}])['request'])
        for extra in ({'value': 'x'}, {'key': 'Space'}, {'holdMs': 100}, {'expectedText': 'x'}, {'paint': {}}):
            with self.subTest(extra=extra), self.assertRaises(protocol.Invalid):
                protocol.validate_request(bundle('<canvas id=game>', [{**painted(), **extra}])['request'])

    def test_scope_names_paint_checks_in_a_fixed_order(self):
        self.assertEqual(protocol.inspection_scope({'steps': [painted()]}), protocol.STEP_SCOPE + protocol.PAINT_SCOPE)
        mixed = {'steps': [{'action': 'select-option'}, {'action': 'fill'}, press(), painted(), {'action': 'download'}]}
        self.assertEqual(protocol.inspection_scope(mixed), protocol.STEP_SCOPE + protocol.SELECT_OPTION_SCOPE + protocol.PRESS_FILL_SCOPE
                         + protocol.PRESS_SCOPE + protocol.PAINT_SCOPE + protocol.DOWNLOAD_SCOPE)
        # Without presses the fill sentence is the one older capsules return.
        self.assertEqual(protocol.inspection_scope({'steps': [{'action': 'fill'}, painted()]}),
                         protocol.STEP_SCOPE + protocol.FILL_SCOPE + protocol.PAINT_SCOPE)
        self.assertNotIn(protocol.PAINT_SCOPE, protocol.inspection_scope({'steps': [text('Ready')]}))
        # The scope never overclaims: a blank detector, not image recognition.
        self.assertIn('not image recognition', protocol.PAINT_SCOPE)
        # Nor does its base deny the paint check it reports.
        self.assertNotIn('not pixel paint', protocol.inspection_scope({'steps': [painted()]}))


class PaintCapability(unittest.TestCase):
    def test_image_without_the_label_is_refused_before_reading_the_snapshot(self):
        request = bundle('<canvas id=game>', [painted()])['request']
        for label in (b'', b'<no value>\n', b'element-paint-v0\n'):
            with self.subTest(label=label), patch.object(broker, 'bounded_process', return_value=label) as process, \
                    patch.object(broker, 'snapshot_bundle') as snapshot:
                result = broker.inspect_request(request, config())
            self.assertEqual(result['errorCode'], 'unsupported_capability')
            self.assertEqual(result['planSha256'], protocol.plan_hash(request))
            self.assertEqual(result['scope'], protocol.STEP_SCOPE + protocol.PAINT_SCOPE)
            self.assertEqual(process.call_count, 1)
            self.assertIn('org.osmantic.ods.inspection.paint', str(process.call_args))
            snapshot.assert_not_called()

    def test_press_and_paint_labels_are_both_checked(self):
        request = bundle('<canvas id=game>', [press(), painted()])['request']
        with patch.object(broker, 'bounded_process', side_effect=[b'trusted-key-press-v1\n', b'']) as process, \
                patch.object(broker, 'snapshot_bundle') as snapshot:
            result = broker.inspect_request(request, config())
        self.assertEqual(result['errorCode'], 'unsupported_capability')
        self.assertEqual(process.call_count, 2)
        snapshot.assert_not_called()

    def test_labelled_image_proceeds_to_the_snapshot(self):
        class Reached(Exception):
            pass
        request = bundle('<canvas id=game>', [painted()])['request']
        with patch.object(broker, 'bounded_process', return_value=b'element-paint-v1\n'), \
                patch.object(broker, 'snapshot_bundle', side_effect=Reached), self.assertRaises(Reached):
            broker.inspect_request(request, config())


class PaintClip(unittest.TestCase):
    def test_whole_pixels_inside_the_content_box_inset_by_one_captured_pixel(self):
        self.assertEqual(capsule.paint_clip(box(10.4, 20.6, 110.2, 80.9)),
                         {'x': 12, 'y': 22, 'width': 97, 'height': 57, 'scale': 1})

    def test_the_viewport_clips_the_box(self):
        self.assertEqual(capsule.paint_clip(box(-50, -10, 100, 50)), {'x': 1, 'y': 1, 'width': 98, 'height': 48, 'scale': 1})
        self.assertEqual(capsule.paint_clip(box(300, 700, 500, 900)), {'x': 301, 'y': 701, 'width': 73, 'height': 110, 'scale': 1})
        self.assertIsNone(capsule.paint_clip(box(400, 0, 600, 100)))
        self.assertIsNone(capsule.paint_clip(box(0, 2000, 100, 2100)))

    def test_large_boxes_capture_at_most_256_pixels_per_side(self):
        clip = capsule.paint_clip(box(0, 0, 1024, 512, viewport=(1920, 1080)))
        self.assertEqual(clip, {'x': 4, 'y': 4, 'width': 1016, 'height': 504, 'scale': 0.25})
        for size in ((257, 40), (1920, 1080), (640, 1920), (300, 300)):
            with self.subTest(size=size):
                clip = capsule.paint_clip(box(0, 0, *size, viewport=(1920, 1920)))
                self.assertLessEqual(round(clip['width'] * clip['scale']), capsule.MAX_PAINT_SIDE)
                self.assertLessEqual(round(clip['height'] * clip['scale']), capsule.MAX_PAINT_SIDE)
                # The inset is at least one captured pixel.
                self.assertGreaterEqual(clip['x'] * clip['scale'], 1)

    def test_rounded_corners_stay_outside_the_clip(self):
        for size, radius_x, radius_y in ((100, 20, 20), (100, 50, 50), (200, 40, 10), (60, 30, 5)):
            with self.subTest(radius=(radius_x, radius_y)):
                clip = capsule.paint_clip(box(0, 0, size, size, radius_x, radius_y))
                # The clip's top-left corner lies inside the corner ellipse.
                dx, dy = (radius_x - clip['x']) / radius_x, (radius_y - clip['y']) / radius_y
                self.assertLessEqual(max(dx, 0) ** 2 + max(dy, 0) ** 2, 1)

    def test_too_small_or_too_thin_is_not_captured(self):
        self.assertIsNone(capsule.paint_clip(box(0, 0, 7.9, 100)))
        self.assertIsNone(capsule.paint_clip(box(0, 0, 100, 7)))
        self.assertEqual(capsule.paint_clip(box(0, 0, 8, 8)), {'x': 1, 'y': 1, 'width': 6, 'height': 6, 'scale': 1})
        self.assertIsNone(capsule.paint_clip(box(0, 0, 1920, 12, viewport=(1920, 1080))))
        # A circle of 10 CSS pixels keeps less than 8x8 inside its curve.
        self.assertIsNone(capsule.paint_clip(box(0, 0, 10, 10, 5)))


class PaintSummary(unittest.TestCase):
    def test_uniform_capture(self):
        self.assertEqual(capsule.paint_summary(BLACK), {
            'width': 16, 'height': 12, 'distinctColors': 1, 'dominant': {'hex': '#000000', 'percent': 100},
            'colors': [{'name': 'black', 'hex': '#000000', 'percent': 100}],
            'fingerprint': hashlib.sha256(BLACK).hexdigest()[:16]})

    def test_two_colors_and_a_spatial_fingerprint(self):
        summary = capsule.paint_summary(SCENE)
        self.assertEqual(summary['distinctColors'], 2)
        self.assertEqual(summary['dominant'], {'hex': '#000000', 'percent': 75})
        self.assertEqual([c['percent'] for c in summary['colors']], [75, 25])
        # Same colors elsewhere in the box: same statistics, another fingerprint.
        moved = capsule.paint_summary(OTHER)
        self.assertEqual({**moved, 'fingerprint': ''}, {**summary, 'fingerprint': ''})
        self.assertNotEqual(moved['fingerprint'], summary['fingerprint'])

    def test_bounded_aggregates_only(self):
        noisy = encode_png(70, 70, [(x % 256, x // 256, 7) for x in range(4900)])
        summary = capsule.paint_summary(noisy)
        self.assertEqual(summary['distinctColors'], capsule.MAX_PAINT_DISTINCT_COLORS)
        self.assertLessEqual(len(summary['colors']), capsule.MAX_PAINT_COLORS)
        self.assertEqual(set(summary), {'width', 'height', 'distinctColors', 'dominant', 'colors', 'fingerprint'})
        self.assertLess(len(protocol.canonical(summary)), 400)

    def test_oversized_or_malformed_captures_are_refused(self):
        for image in (encode_png(257, 1, [(0, 0, 0)] * 257), encode_png(1, 257, [(0, 0, 0)] * 257), b'not a png',
                      BLACK[:8] + harness.png_chunk(b'tEXt', b'x') + BLACK[8:]):
            with self.subTest(size=len(image)), self.assertRaises(protocol.Invalid):
                capsule.paint_summary(image)


class PaintPolling(unittest.TestCase):
    """paint_until_varied: positive samples end early; timeouts never pass."""

    def poll(self, values, cost=0.0):
        clock, waits, samples = [0.0], [], iter(values)

        def sample():
            clock[0] += cost
            return next(samples)

        def wait(milliseconds):
            waits.append(milliseconds)
            clock[0] += milliseconds / 1000

        with patch.object(capsule.time, 'monotonic', side_effect=lambda: clock[0]):
            result = capsule.paint_until_varied(sample, wait)
        return result, waits, clock[0]

    def test_first_varied_sample_returns_at_once(self):
        self.assertEqual(self.poll([{'distinctColors': 3}]), ({'distinctColors': 3}, [], 0.0))

    def test_a_late_first_frame_is_awaited(self):
        result, waits, _ = self.poll([None, {'distinctColors': 1}, {'distinctColors': 1}, {'distinctColors': 2}])
        self.assertEqual(result, {'distinctColors': 2})
        self.assertEqual(waits, [100, 100, 100])

    def test_uniform_or_absent_paint_stays_failed_until_the_window_ends(self):
        for value in ({'distinctColors': 1}, None):
            with self.subTest(value=value):
                result, waits, elapsed = self.poll(itertools.repeat(value))
                self.assertEqual(result, value)
                self.assertGreaterEqual(len(waits), 13)
                self.assertLessEqual(elapsed, capsule.PAINT_WINDOW_S + 1e-9)

    def test_slow_samples_stay_within_the_window_plus_one_sample(self):
        _, _, elapsed = self.poll(itertools.repeat({'distinctColors': 1}), cost=0.4)
        self.assertLessEqual(elapsed, capsule.PAINT_WINDOW_S + 0.4 + 1e-9)


def frames(*images):
    """A render that returns each image once, then repeats the last."""
    queue = list(images)
    return lambda _browser: queue.pop(0) if len(queue) > 1 else queue[0]


class PaintBrowser(harness.ScriptedBrowser):
    """Scripted double with element geometry and CDP captures; `render`
    returns the PNG of each capture. It also serves as the capsule's clock:
    wait_for_timeout advances it. The real capture is covered by RealPaint."""

    def __init__(self, render, geometry=None, during_capture=None, **kwargs):
        super().__init__(**kwargs)
        self.render, self.geometry, self.during_capture = render, geometry or box(10, 20, 330, 260), during_capture
        # gone: 'hidden' or 'removed' makes the element stop being one visible match.
        self.captures, self.now, self.gone = [], 0.0, None

    def monotonic(self):
        return self.now

    def wait_for_timeout(self, milliseconds):
        self.now += milliseconds / 1000

    def route(self, _pattern, handler):
        self.guard = handler

    def goto(self, url, **kwargs):
        self.origin = url.split('/__ods_inspection__.html')[0]
        super().goto(url, **kwargs)

    def send(self, method, params=None):
        if method == 'Page.captureScreenshot':
            self.captures.append(params)
            if self.during_capture:
                self.during_capture(self)
            return {'data': base64.b64encode(self.render(self)).decode()}
        if self.gone == 'removed' and (params or {}).get('functionDeclaration') == capsule.SELECTOR_COUNT:
            return {'result': {'value': {'count': 0}}}
        result = super().send(method, params)
        if (params or {}).get('functionDeclaration') == capsule.OBSERVE_ELEMENT:
            if params['arguments'][5]['value']:
                result['result']['value']['box'] = dict(self.geometry)
            if self.gone == 'hidden':
                result['result']['value']['visible'] = False
        return result


class ScriptedPaint(unittest.TestCase):
    def run_paint(self, browser, steps):
        data = bundle('<canvas id=game></canvas><p id=item hidden></p><button id=start>Start</button>', steps)
        browser.site = data['request']['siteId']
        with patch.object(capsule, 'time', browser):
            result = capsule.run_browser(data, playwright_factory=browser)
        self.assertLessEqual(len(protocol.canonical(result)), protocol.MAX_RESULT)
        return result

    def test_capture_is_one_bounded_cdp_clip_and_only_aggregates_leave(self):
        browser = PaintBrowser(frames(SCENE))
        result = self.run_paint(browser, [painted()])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['scope'], protocol.STEP_SCOPE + protocol.PAINT_SCOPE)
        self.assertEqual(browser.captures, [{'format': 'png', 'captureBeyondViewport': False,
                                             'clip': {'x': 12, 'y': 22, 'width': 316, 'height': 236, 'scale': 0.8}}])
        step = result['steps'][0]
        self.assertEqual(set(step), {'index', 'action', 'locator', 'before', 'stable', 'status', 'paint'})
        self.assertEqual(step['paint'], capsule.paint_summary(SCENE))
        # The geometry decides the clip; neither it nor any image byte is evidence.
        encoded = json.dumps(result)
        self.assertNotIn(base64.b64encode(SCENE).decode()[:24], encoded)
        self.assertNotIn('"box"', encoded)

    def test_uniform_paint_fails_after_sampling_the_whole_window(self):
        browser = PaintBrowser(frames(BLACK))
        result = self.run_paint(browser, [painted(), harness.step('assert-visible', '#game')])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['steps']), 1, 'later steps do not run')
        step = result['steps'][0]
        self.assertEqual((step['status'], step['errorCode']), ('failed', 'uniform_paint'))
        self.assertEqual((step['paint']['distinctColors'], step['paint']['dominant']['hex']), (1, '#000000'))
        self.assertTrue(13 <= len(browser.captures) <= 16, len(browser.captures))

    def test_a_canvas_that_draws_on_a_later_frame_passes(self):
        browser = PaintBrowser(frames(BLACK, BLACK, SCENE))
        result = self.run_paint(browser, [painted()])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(len(browser.captures), 3)
        self.assertEqual(result['steps'][0]['paint']['fingerprint'], hashlib.sha256(SCENE).hexdigest()[:16])

    def test_an_element_outside_the_viewport_is_never_captured(self):
        browser = PaintBrowser(frames(SCENE), geometry=box(0, 900, 300, 1200))
        step = self.run_paint(browser, [painted()])['steps'][0]
        self.assertEqual(step['errorCode'], 'not_in_viewport')
        self.assertNotIn('paint', step)
        self.assertEqual(browser.captures, [])

    def test_an_element_that_stops_being_one_visible_match_while_sampling_captures_nothing(self):
        # A splash canvas that hides or removes itself after one uniform
        # sample: every later sample is empty, and the last one decides.
        for gone in ('hidden', 'removed'):
            with self.subTest(gone=gone):
                def vanish(browser, gone=gone):
                    browser.gone = gone
                browser = PaintBrowser(frames(BLACK), during_capture=vanish)
                result = self.run_paint(browser, [painted(), harness.step('assert-visible', '#item')])
                step = result['steps'][0]
                self.assertEqual((result['status'], len(result['steps'])), ('failed', 1))
                self.assertEqual((step['status'], step['errorCode']), ('failed', 'not_in_viewport'))
                self.assertNotIn('paint', step)
                self.assertEqual(len(browser.captures), 1)

    def test_a_fully_visible_long_thin_bar_is_too_thin_to_capture(self):
        # 1000x8 CSS pixels pass the 8x8 minimum, but downscaled by 0.256 the
        # one-captured-pixel inset (4 CSS pixels per side) leaves nothing.
        browser = PaintBrowser(frames(SCENE), geometry=box(0, 0, 1000, 8, viewport=(1280, 720)))
        step = self.run_paint(browser, [painted()])['steps'][0]
        self.assertEqual(step['errorCode'], 'not_in_viewport')
        self.assertEqual(browser.captures, [])

    def test_a_hidden_element_is_a_visibility_mismatch_without_capture(self):
        browser = PaintBrowser(frames(SCENE))
        step = self.run_paint(browser, [painted('#item')])['steps'][0]
        self.assertEqual(step['errorCode'], 'visibility_mismatch')
        self.assertEqual(browser.captures, [])

    def test_paint_shows_the_state_after_an_earlier_click(self):
        render = lambda browser: SCENE if browser.revealed else BLACK
        result = self.run_paint(PaintBrowser(render), [harness.step('click', '#start'), painted()])
        self.assertEqual(result['status'], 'passed', result)
        result = self.run_paint(PaintBrowser(render), [painted(), harness.step('click', '#start')])
        self.assertEqual(result['steps'][0]['errorCode'], 'uniform_paint')

    def test_a_request_blocked_while_sampling_is_never_a_paint_verdict(self):
        def navigate(browser):
            browser.guard(harness.Route(browser.origin + '/outside?token=secret-canary', navigation=True))
        with self.assertRaises(capsule.BlockedRequest):
            self.run_paint(PaintBrowser(frames(BLACK), during_capture=navigate), [painted()])

    def test_twelve_paint_steps_keep_the_receipt_bounded(self):
        noisy = encode_png(70, 70, [(x % 256, x // 256, 7) for x in range(4900)])
        result = self.run_paint(PaintBrowser(frames(noisy)), [painted()] * protocol.MAX_STEPS)
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(len(result['steps']), protocol.MAX_STEPS)


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS') == '1' or os.environ.get('ODS_INSPECTION_TEST_IMAGE'), 'requires isolated real browser')
class RealPaint(unittest.TestCase):
    SPRITE = '''<canvas id="game" width="240" height="160" style="background:#000"></canvas><script>
    const context = document.querySelector('#game').getContext('2d');
    let x = 20;
    function draw() { context.clearRect(0, 0, 240, 160); context.fillStyle = '#e53935'; context.fillRect(x, 60, 30, 30); }
    draw();
    window.addEventListener('keydown', event => { if (event.key === 'ArrowRight') { x += 60; draw(); } });
    </script>'''

    def test_a_black_canvas_that_never_draws_is_uniform(self):
        html = '<canvas id="game" width="240" height="160" style="background:#000"></canvas>'
        step = execute_real(bundle(html, [painted()]))['steps'][0]
        self.assertEqual(step['errorCode'], 'uniform_paint', step)
        self.assertEqual(step['paint']['dominant'], {'hex': '#000000', 'percent': 100})

    def test_a_drawn_2d_canvas_passes(self):
        result = execute_real(bundle(self.SPRITE, [painted()]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertGreaterEqual(result['steps'][0]['paint']['distinctColors'], 2)

    def test_a_canvas_that_draws_after_its_first_frames_passes(self):
        html = '''<canvas id="game" width="240" height="160" style="background:#000"></canvas><script>
        setTimeout(() => { const c = document.querySelector('#game').getContext('2d'); c.fillStyle = '#fff'; c.fillRect(10, 10, 50, 50); }, 400);
        </script>'''
        result = execute_real(bundle(html, [painted()]))
        self.assertEqual(result['status'], 'passed', result)

    def test_an_overlay_is_measured_as_what_is_on_screen(self):
        html = self.SPRITE + '<div style="position:fixed;inset:0;background:#123456"></div>'
        step = execute_real(bundle(html, [painted()]))['steps'][0]
        self.assertEqual(step['errorCode'], 'uniform_paint', step)
        self.assertEqual(step['paint']['dominant']['hex'], '#123456')

    def test_rounded_corners_of_a_black_canvas_do_not_count_as_paint(self):
        html = '<body style="background:#fff"><canvas id="game" width="240" height="160" style="background:#000;border-radius:40px;border:6px solid #e53935;padding:4px"></canvas>'
        step = execute_real(bundle(html, [painted()]))['steps'][0]
        self.assertEqual(step['errorCode'], 'uniform_paint', step)

    def test_an_element_below_the_viewport_is_not_in_viewport(self):
        html = '<div style="height:2000px"></div><canvas id="game" width="240" height="160" style="background:#000"></canvas>'
        step = execute_real(bundle(html, [painted()]))['steps'][0]
        self.assertEqual(step['errorCode'], 'not_in_viewport', step)

    def test_a_large_canvas_is_captured_downscaled(self):
        html = '<canvas id="game" width="1200" height="900" style="display:block;background:linear-gradient(#000,#fff)"></canvas>'
        result = execute_real(bundle(html, [painted()], viewport={'width': 1280, 'height': 960}))
        self.assertEqual(result['status'], 'passed', result)
        paint = result['steps'][0]['paint']
        self.assertLessEqual(max(paint['width'], paint['height']), capsule.MAX_PAINT_SIDE)

    def test_a_key_press_that_moves_a_sprite_changes_the_fingerprint(self):
        result = execute_real(bundle(self.SPRITE, [painted(), press(), painted()]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertNotEqual(result['steps'][0]['paint']['fingerprint'], result['steps'][2]['paint']['fingerprint'])

    def test_capture_changes_no_style_scroll_or_viewport(self):
        # A downscaled clip (scale < 1) on a page scrolled to 100: the page
        # observes no resize, scroll or DOM change, and keeps its position.
        html = '''<style>body{margin:0;height:3000px;background:#fff}#game{display:block;margin-top:150px;background:#123}</style>
        <canvas id="game" width="1000" height="700"></canvas><p id="events">pending</p><script>
        const game = document.querySelector('#game').getContext('2d');
        game.fillStyle = '#e53935'; game.fillRect(100, 100, 200, 200);
        const events = document.querySelector('#events'), seen = {resize: 0, scroll: 0, mutation: 0};
        scrollTo(0, 100);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          for (const target of [window, visualViewport]) for (const kind of ['resize', 'scroll'])
            target.addEventListener(kind, () => seen[kind]++);
          new MutationObserver(list => { seen.mutation += list.filter(m => !events.contains(m.target)).length; })
            .observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
          (function report() {
            const line = `resize=${seen.resize} scroll=${seen.scroll} mutation=${seen.mutation} y=${scrollY}`;
            if (events.textContent !== line) events.textContent = line;
            requestAnimationFrame(report);
          })();
        }));
        </script>'''
        result = execute_real(bundle(html, [text('resize=0 scroll=0 mutation=0 y=100', '#events'), painted(),
                                            text('resize=0 scroll=0 mutation=0 y=100', '#events')],
                                     viewport={'width': 1280, 'height': 960}))
        self.assertEqual(result['status'], 'passed', result)
        self.assertLess(result['steps'][1]['paint']['width'], 1000, 'the clip was downscaled')

    def test_webgl_canvas_paint_tracks_webgl_availability(self):
        # Documents what the GPU-less capsule renders: a WebGL scene must
        # pass when the page obtained a context, and reads as the page
        # background (uniform_paint) only when no context was available.
        html = '''<body style="background:#fff"><canvas id="game" width="200" height="150"></canvas><p id="gl">none</p><script>
        const gl = document.querySelector('#game').getContext('webgl', {preserveDrawingBuffer: true});
        if (gl) {
          gl.clearColor(0.1, 0.2, 0.6, 1); gl.clear(gl.COLOR_BUFFER_BIT);
          gl.enable(gl.SCISSOR_TEST); gl.scissor(20, 20, 60, 60); gl.clearColor(0.9, 0.2, 0.2, 1); gl.clear(gl.COLOR_BUFFER_BIT);
          document.querySelector('#gl').textContent = 'webgl';
        }</script>'''
        available = execute_real(bundle(html, [text('webgl', '#gl')]))
        paint = execute_real(bundle(html, [painted()]))
        self.assertEqual(paint['status'], available['status'], (available, paint))
        if paint['status'] == 'failed':
            self.assertEqual(paint['steps'][0]['errorCode'], 'uniform_paint', paint)


if __name__ == '__main__':
    unittest.main()
