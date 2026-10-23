"""Trusted key presses on the published snapshot, never a text-entry path."""
import json
import os
import unittest
from unittest.mock import patch

import test_preview_inspection as harness
from test_preview_inspection_blocked import execute_real

bundle, protocol, broker, capsule = harness.bundle, harness.protocol, harness.broker, harness.capsule


def press(selector='#game', key='Space', **extra):
    return {'action': 'press', 'locator': {'selector': selector}, 'key': key, **extra}


def text(value, selector='h1'):
    return {'action': 'assert-text', 'locator': {'selector': selector}, 'expectedText': value}


def config():
    return {'ownerUid': os.getuid(), 'docker': '/usr/bin/docker', 'transport': 'local',
            'snapshotRoot': '/unused', 'imageId': 'sha256:' + 'a' * 64}


class PressProtocol(unittest.TestCase):
    def test_every_allowlisted_key_and_bounded_hold(self):
        for key in protocol.PRESS_KEYS:
            protocol.validate_request(bundle('<canvas id=game>', [press(key=key)])['request'])
        for hold in (0, 100, protocol.MAX_PRESS_HOLD_MS):
            protocol.validate_request(bundle('<canvas id=game>', [press(holdMs=hold)])['request'])
        self.assertEqual(len(protocol.PRESS_KEYS), 44)
        self.assertEqual(len(set(protocol.PRESS_KEYS)), 44)

    def test_modifiers_chords_text_and_unbounded_holds_are_rejected(self):
        for key in ('Control+a', 'Meta', 'Shift', 'F5', 'hello', 'A', 'KeyA', 'Digit1', 'space',
                    'ArrowRight+Space', ' ', '', '\n', 1, None, ['a']):
            with self.subTest(key=key), self.assertRaises(protocol.Invalid):
                protocol.validate_request(bundle('<canvas id=game>', [press(key=key)])['request'])
        for hold in (-1, protocol.MAX_PRESS_HOLD_MS + 1, 1.5, True, '100', None):
            with self.subTest(hold=hold), self.assertRaises(protocol.Invalid):
                protocol.validate_request(bundle('<canvas id=game>', [press(holdMs=hold)])['request'])

    def test_press_fields_are_exact_and_belong_only_to_press(self):
        missing = press()
        del missing['key']
        for step in (missing, press(value='a'), press(expectedText='a'), press(modifiers=['Shift']),
                     {**harness.step('click', '#game'), 'key': 'a'},
                     {**harness.step('assert-visible', '#game'), 'holdMs': 100}):
            with self.subTest(step=step), self.assertRaises(protocol.Invalid):
                protocol.validate_request(bundle('<canvas id=game>', [step])['request'])

    def test_scope_names_presses_in_a_fixed_order(self):
        request = {'steps': [press(), text('Ready')]}
        self.assertEqual(protocol.inspection_scope(request), protocol.STEP_SCOPE + protocol.PRESS_SCOPE)
        mixed = {'steps': [{'action': 'select-option'}, {'action': 'fill'}, press(), {'action': 'download'}]}
        self.assertEqual(protocol.inspection_scope(mixed), protocol.STEP_SCOPE + protocol.SELECT_OPTION_SCOPE
                         + protocol.PRESS_FILL_SCOPE + protocol.PRESS_SCOPE + protocol.DOWNLOAD_SCOPE)
        self.assertNotIn(protocol.PRESS_SCOPE, protocol.inspection_scope({'steps': [text('Ready')]}))

    def test_press_scope_never_denies_the_presses_it_names(self):
        # Fill a name, then press keys: the receipt states what each did.
        scope = protocol.inspection_scope({'steps': [{'action': 'fill'}, press(), text('Ready')]})
        self.assertEqual(scope, protocol.STEP_SCOPE + protocol.PRESS_FILL_SCOPE + protocol.PRESS_SCOPE)
        self.assertNotIn('does not test keyboard behavior', scope)
        self.assertNotIn('click dispatches were tested', scope)
        self.assertIn('Fill sends no key events', scope)
        # Plans older capsules also run keep their exact strings.
        self.assertEqual(protocol.inspection_scope({'steps': [{'action': 'fill'}, text('Ready')]}),
                         protocol.SCOPE + protocol.FILL_SCOPE)
        self.assertEqual(protocol.inspection_scope({'steps': [{'action': 'select-option'}, text('Ready')]}),
                         protocol.SELECT_SCOPE)


class PressCapability(unittest.TestCase):
    def test_image_without_the_label_is_refused_before_reading_the_snapshot(self):
        request = bundle('<canvas id=game>', [press(), text('Ready')])['request']
        for label in (b'', b'<no value>\n', b'trusted-key-press-v0\n'):
            with self.subTest(label=label), patch.object(broker, 'bounded_process', return_value=label) as process, \
                    patch.object(broker, 'snapshot_bundle') as snapshot:
                result = broker.inspect_request(request, config())
            self.assertEqual(result['errorCode'], 'unsupported_capability')
            self.assertEqual(result['planSha256'], protocol.plan_hash(request))
            self.assertEqual(result['scope'], protocol.STEP_SCOPE + protocol.PRESS_SCOPE)
            self.assertEqual(process.call_count, 1)
            self.assertIn('org.osmantic.ods.inspection.press', str(process.call_args))
            snapshot.assert_not_called()

    def test_each_capability_of_a_mixed_plan_is_checked(self):
        request = bundle('<input id=task>', [{'action': 'fill', 'locator': {'selector': '#task'}, 'value': 'x'},
                                             press('#game')])['request']
        with patch.object(broker, 'bounded_process', side_effect=[b'native-text-number-fill-v2\n', b'']) as process, \
                patch.object(broker, 'snapshot_bundle') as snapshot:
            result = broker.inspect_request(request, config())
        self.assertEqual(result['errorCode'], 'unsupported_capability')
        self.assertEqual(process.call_count, 2)
        snapshot.assert_not_called()

    def test_labelled_image_proceeds_to_the_snapshot(self):
        class Reached(Exception):
            pass
        request = bundle('<canvas id=game>', [press()])['request']
        with patch.object(broker, 'bounded_process', return_value=b'trusted-key-press-v1\n'), \
                patch.object(broker, 'snapshot_bundle', side_effect=Reached), self.assertRaises(Reached):
            broker.inspect_request(request, config())


class Download:
    def cancel(self):
        self.cancelled = True


class PressBrowser(harness.ScriptedBrowser):
    """Scripted double with focus and a keyboard: a key press reveals #item.
    The real focus path is covered by the opt-in RealPress cases below."""

    def __init__(self, target_editable=False, active_editable=False, document_focus=True, during_press=None,
                 shadow_root=None, focused_error=False, **kwargs):
        super().__init__(**kwargs)
        self.target_editable, self.active_editable, self.document_focus = target_editable, active_editable, document_focus
        self.during_press, self.keyboard, self.probes = during_press, self, []
        # shadow_root: the type of the root CDP reports on the focused element.
        self.shadow_root, self.focused_error = shadow_root, focused_error

    def route(self, _pattern, handler):
        self.guard = handler

    def goto(self, url, **kwargs):
        self.origin = url.split('/__ods_inspection__.html')[0]
        super().goto(url, **kwargs)

    def locator(self, selector):
        self.selector = selector
        return self

    def focus(self, **_):
        self.calls.append('focus:' + self.selector)

    def press(self, key, delay=None):
        self.calls.append(f'press:{key}:{delay}')
        self.revealed = True
        if self.during_press:
            self.during_press(self)
        self.emit(self.click_errors)

    def send(self, method, params=None):
        params = params or {}
        if params.get('functionDeclaration') == capsule.FOCUSED_ELEMENT:
            if self.focused_error:
                return {'result': {'type': 'object'}, 'exceptionDetails': {'text': 'Uncaught'}}
            return {'result': {'type': 'object', 'objectId': 'focused'}}
        if method == 'DOM.describeNode':
            self.calls.append('describe:' + params['objectId'] + (':pierce' if params.get('pierce') else ''))
            roots = [{'nodeName': '#document-fragment', 'shadowRootType': self.shadow_root}] if self.shadow_root else []
            return {'node': {'nodeName': 'X-FIELD', **({'shadowRoots': roots} if roots else {})}}
        if method == 'Runtime.releaseObject' and params['objectId'] == 'focused':
            self.calls.append('release:focused')
        result = super().send(method, params)
        if params.get('functionDeclaration') == capsule.OBSERVE_ELEMENT and params['arguments'][4]['value']:
            self.probes.append(params['arguments'][4]['value'])
            focused = 'focus:css=#game' in self.calls
            result['result']['value']['focus'] = {'target': self.target_editable, 'active': self.active_editable and focused,
                                                  'element': focused, 'document': self.document_focus and focused}
        return result


class ScriptedPress(unittest.TestCase):
    def run_press(self, browser, steps=None):
        data = bundle('<canvas id=game></canvas><p id=item hidden></p>', steps or [
            harness.step('assert-hidden', '#item'), press(), harness.step('assert-visible', '#item')])
        browser.site = data['request']['siteId']
        result = capsule.run_browser(data, playwright_factory=browser)
        self.assertLessEqual(len(protocol.canonical(result)), protocol.MAX_RESULT)
        return result

    def test_frame_then_target_focus_precede_one_trusted_press(self):
        browser = PressBrowser()
        result = self.run_press(browser)
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['scope'], protocol.STEP_SCOPE + protocol.PRESS_SCOPE)
        actions = [c for c in browser.calls if c.startswith(('focus:', 'describe:', 'release:', 'press:'))]
        # CDP describes the focused element, closed shadow roots included,
        # after focusing and before the key; its handle is released.
        self.assertEqual(actions, ['focus:iframe[name="inspection"]', 'focus:css=#game', 'describe:focused:pierce',
                                   'release:focused', 'press:Space:100'])
        # Focus held elsewhere is released before focusing; the second probe only reads.
        self.assertEqual(browser.probes, ['release', 'observe'])
        step = result['steps'][1]
        self.assertEqual(set(step), {'index', 'action', 'locator', 'key', 'before', 'focus', 'after', 'stable', 'status'})
        self.assertEqual(step['focus'], {'element': True, 'document': True})
        # The editability probes decide the refusal; they are not evidence.
        self.assertNotIn('"target"', json.dumps(result))
        self.assertNotIn('"active"', json.dumps(result))

    def test_hold_is_the_requested_key_down_time(self):
        for hold in (0, 1000):
            browser = PressBrowser()
            result = self.run_press(browser, [press(holdMs=hold), harness.step('assert-visible', '#item')])
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(result['steps'][0]['holdMs'], hold)
            self.assertIn(f'press:Space:{hold}', browser.calls)

    def test_editable_target_is_refused_before_any_focus_or_key(self):
        browser = PressBrowser(target_editable=True)
        result = self.run_press(browser)
        step = result['steps'][1]
        self.assertEqual((result['status'], step['status'], step['errorCode']), ('failed', 'failed', 'text_field_press_unsupported'))
        self.assertNotIn('focus', step)
        self.assertFalse([c for c in browser.calls if c.startswith(('focus:', 'press:'))])

    def test_focus_moved_to_an_editable_element_is_refused_before_the_key(self):
        browser = PressBrowser(active_editable=True)
        step = self.run_press(browser)['steps'][1]
        self.assertEqual(step['errorCode'], 'text_field_press_unsupported')
        self.assertEqual(step['focus'], {'element': True, 'document': True})
        self.assertFalse([c for c in browser.calls if c.startswith('press:')])

    def test_focus_hidden_in_a_closed_shadow_root_is_refused_before_the_key(self):
        # document.activeElement stops at the host (a delegatesFocus web
        # component field); the isolated world cannot see into a closed root.
        for root, refused in (('closed', True), ('open', False), ('user-agent', False)):
            with self.subTest(root=root):
                browser = PressBrowser(shadow_root=root)
                step = self.run_press(browser)['steps'][1]
                self.assertEqual(step.get('errorCode'), 'text_field_press_unsupported' if refused else None, step)
                self.assertEqual(step['focus'], {'element': True, 'document': True})
                self.assertEqual(bool([c for c in browser.calls if c.startswith('press:')]), not refused)
                self.assertIn('release:focused', browser.calls)

    def test_an_unreadable_focused_element_fails_closed_without_a_key(self):
        browser = PressBrowser(focused_error=True)
        step = self.run_press(browser)['steps'][1]
        self.assertEqual(step['errorCode'], 'press_failed')
        self.assertFalse([c for c in browser.calls if c.startswith(('describe:', 'press:'))])

    def test_unfocused_document_fails_closed_without_a_key(self):
        browser = PressBrowser(document_focus=False)
        step = self.run_press(browser)['steps'][1]
        self.assertEqual(step['errorCode'], 'focus_failed')
        self.assertEqual(step['focus'], {'element': True, 'document': False})
        self.assertFalse([c for c in browser.calls if c.startswith('press:')])

    def test_hidden_target_is_not_pressed(self):
        browser = PressBrowser()
        result = self.run_press(browser, [press('#item')])
        self.assertEqual(result['steps'][0]['errorCode'], 'press_failed')
        self.assertFalse([c for c in browser.calls if c.startswith(('focus:', 'press:'))])

    def test_navigation_from_a_key_is_a_failed_press_with_the_block_recorded(self):
        def navigate(browser):
            browser.guard(harness.Route(browser.origin + '/outside?token=secret-canary', navigation=True))
        result = self.run_press(PressBrowser(during_press=navigate))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['steps'][1]['errorCode'], 'press_failed')
        self.assertEqual(result['blockedRequests'], ['navigation'])
        self.assertEqual(len(result['steps']), 2)
        self.assertNotIn('secret-canary', json.dumps(result))

    def test_download_from_a_key_is_an_unexpected_download(self):
        download = Download()

        def start(browser):
            for handler in browser.handlers['download']:
                handler(download)
        result = self.run_press(PressBrowser(during_press=start))
        self.assertEqual(result['steps'][1]['errorCode'], 'unexpected_download')
        self.assertEqual(result['blockedRequests'], ['download'])
        self.assertTrue(download.cancelled)

    def test_page_error_after_a_key_is_recorded_without_changing_status(self):
        browser = PressBrowser(click_errors=[harness.PageError('TypeError', 'frame failed after key')])
        result = self.run_press(browser)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['pageErrors'], {'count': 1, 'messages': ['TypeError: frame failed after key']})


PDF = b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n'


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS') == '1' or os.environ.get('ODS_INSPECTION_TEST_IMAGE'), 'requires isolated real browser')
class RealPress(unittest.TestCase):
    def test_focusable_canvas_receives_trusted_space_without_a_prior_click(self):
        html = '''<canvas id="game" tabindex="0" width="64" height="64"></canvas><h1>Paused</h1><script>
        const title = document.querySelector('h1');
        document.querySelector('#game').addEventListener('keydown', event => {
          if (event.isTrusted && event.code === 'Space') title.textContent = title.textContent === 'Paused' ? 'Running' : 'Paused';
        });</script>'''
        result = execute_real(bundle(html, [text('Paused'), press(), text('Running'), press(), text('Paused')]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['steps'][1]['focus'], {'element': True, 'document': True})

    def test_window_listener_hears_keys_sent_to_an_unfocusable_canvas(self):
        html = '''<canvas id="game" width="64" height="64"></canvas><p id="x">0</p><script>
        let x = 0;
        window.addEventListener('keydown', event => {
          if (event.isTrusted && event.key === 'ArrowRight') document.querySelector('#x').textContent = String(++x);
        });</script>'''
        result = execute_real(bundle(html, [text('0', '#x'), press(key='ArrowRight'), press(key='ArrowRight'), text('2', '#x')]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['steps'][1]['focus'], {'element': False, 'document': True})

    def test_space_on_an_unfocusable_canvas_does_not_activate_a_button_clicked_earlier(self):
        html = '''<button id="start">Start</button><canvas id="game" width="64" height="64"></canvas><p id="state">0/0</p><script>
        let starts = 0, jumps = 0;
        const render = () => { document.querySelector('#state').textContent = starts + '/' + jumps; };
        document.querySelector('#start').addEventListener('click', () => { starts++; render(); });
        window.addEventListener('keydown', event => { if (event.code === 'Space') { jumps++; render(); } });
        </script>'''
        result = execute_real(bundle(html, [{'action': 'click', 'locator': {'selector': '#start'}}, text('1/0', '#state'),
                                            press(), text('1/1', '#state')]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['steps'][2]['focus'], {'element': False, 'document': True})

    def test_held_key_moves_a_frame_loop(self):
        html = '''<canvas id="game" width="64" height="64"></canvas><p id="state">idle</p><script>
        let held = false, frames = 0;
        document.addEventListener('keydown', event => { if (event.key === 'ArrowRight') held = true; });
        document.addEventListener('keyup', event => { if (event.key === 'ArrowRight') held = false; });
        (function loop() {
          if (held && ++frames >= 3) document.querySelector('#state').textContent = 'moved';
          requestAnimationFrame(loop);
        })();</script>'''
        result = execute_real(bundle(html, [text('idle', '#state'), press(key='ArrowRight', holdMs=400), text('moved', '#state')]))
        self.assertEqual(result['status'], 'passed', result)

    def test_text_entry_targets_and_redirected_focus_are_refused(self):
        for html in ('<input id="game" type="password" value="PRIVATE">', '<input id="game" value="PRIVATE">',
                     '<input id="game" type="date">', '<textarea id="game">PRIVATE</textarea>',
                     '<div id="game" contenteditable>PRIVATE</div>', '<select id="game"><option>PRIVATE</option></select>',
                     '<button id="game">Go</button><input id="secret" type="password"><script>'
                     "game.addEventListener('focus', () => secret.focus());</script>",
                     # Web-component fields: document.activeElement is only the host.
                     *('<div id="game"></div><script>game.attachShadow({mode: "%s", delegatesFocus: true})'
                       ".innerHTML = '<input type=\"password\" value=\"PRIVATE\">';</script>" % mode
                       for mode in ('open', 'closed')),
                     '<canvas id="game" tabindex="0" width="64" height="64"></canvas><div id="field"></div><script>'
                     "const root = field.attachShadow({mode: 'closed'}); root.innerHTML = '<input value=\"PRIVATE\">';"
                     "game.addEventListener('focus', () => root.querySelector('input').focus());</script>"):
            with self.subTest(html=html):
                result = execute_real(bundle(html, [press(key='a')]))
                self.assertEqual(result['status'], 'failed', result)
                self.assertEqual(result['steps'][0]['errorCode'], 'text_field_press_unsupported', result)
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_enter_on_a_link_cannot_navigate_away(self):
        html = '<a id="game" href="/outside?token=secret-canary">Leave</a><h1>Ready</h1>'
        result = execute_real(bundle(html, [press(key='Enter'), text('Ready')]))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['steps'][0]['errorCode'], 'press_failed', result)
        self.assertIn('navigation', result['blockedRequests'])
        self.assertNotIn('secret-canary', json.dumps(result))

    def test_key_started_download_is_blocked(self):
        html = '''<h1 id="game">Ready</h1><script>document.addEventListener('keydown', event => {
          if (event.key !== 'd') return;
          const link = document.createElement('a');
          link.href = 'artifact.pdf'; link.download = ''; document.body.append(link); link.click();
        });</script>'''
        result = execute_real(bundle('', [press(key='d'), text('Ready')],
                                     files={'index.html': html.encode(), 'artifact.pdf': PDF}))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['steps'][0]['errorCode'], 'unexpected_download', result)
        self.assertIn('download', result['blockedRequests'])

    def test_error_in_the_next_frame_after_a_key_withholds_proof(self):
        html = '''<canvas id="game" width="64" height="64"></canvas><h1>Ready</h1><script>
        document.addEventListener('keydown', () => requestAnimationFrame(() => { throw new Error('frame failed after key'); }));
        </script>'''
        result = execute_real(bundle(html, [press(key='x'), text('Ready')]))
        self.assertEqual(result['status'], 'passed', result)
        self.assertIn('Error: frame failed after key', result['pageErrors']['messages'])


if __name__ == '__main__':
    unittest.main()
