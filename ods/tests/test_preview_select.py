"""Native selects are exercised without redesigning the published website."""
import os
import unittest
from unittest.mock import patch

import test_preview_inspection as harness

bundle, protocol, step, broker = harness.bundle, harness.protocol, harness.step, harness.broker


HTML = '''<label for="customer">Cliente</label><select id="customer">
<option value="">Todos</option><option value="Lia">Lia</option><option value="Hugo">Hugo</option>
</select><output id="total">52.60</output><output id="events">0/0</output>
<script>let inputs=0,changes=0; customer.oninput=()=>inputs++;
customer.onchange=()=>{changes++; events.textContent=inputs+'/'+changes;
setTimeout(()=>total.textContent=({Lia:'43.00',Hugo:'9.60'})[customer.value]||'52.60',150);};</script>'''


def select(value, locator=None):
    return {'action': 'select-option', 'locator': locator or {'selector': '#customer'}, 'value': value}


def text(selector, value):
    return {**step('assert-text', selector), 'expectedText': value}


PLAN = [text('#total', '52.60'), select('Lia'), text('#total', '43.00'),
        select('Hugo'), text('#total', '9.60'), select(''), text('#total', '52.60'), text('#events', '3/3')]


class SelectProtocolTests(unittest.TestCase):
    def test_old_capsule_never_receives_new_action(self):
        request = bundle(HTML, [select('Lia')])['request']
        config = {'docker': '/usr/bin/docker', 'imageId': 'sha256:' + 'a' * 64,
                  'ownerUid': os.getuid(), 'transport': 'local', 'snapshotRoot': '/owned'}
        with patch.object(broker, 'bounded_process', return_value=b'<no value>\n') as run:
            result = broker.inspect_request(request, config)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['errorCode'], 'unsupported_capability')
        self.assertEqual(result['planSha256'], protocol.plan_hash(request))
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('run', run.call_args.args[0])

    def test_value_is_exact_bounded_data(self):
        for value in ('', 'Lia', 'São Paulo', 'a' * 256):
            protocol.validate_request(bundle(HTML, [select(value)])['request'])
        for value in (None, True, 3, ['Lia'], 'x' * 257, '\n', '\x00', '\u2028'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.validate_request(bundle(HTML, [select(value)])['request'])

    def test_selection_fields_are_not_accepted_by_other_actions(self):
        for action in ('click', 'assert-visible', 'assert-hidden'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                protocol.validate_request(bundle(HTML, [{**select('Lia'), 'action': action}])['request'])
        invalid = select('Lia')
        invalid['script'] = 'customer.value="Lia"'
        with self.assertRaises(ValueError):
            protocol.validate_request(bundle(HTML, [invalid])['request'])


class SelectionCases:
    def test_native_select_changes_value_and_dispatches_input_then_change(self):
        result = self.run_site(HTML, PLAN)
        self.assertEqual(result['status'], 'passed', result)
        selected = [s for s in result['steps'] if s['action'] == 'select-option']
        self.assertEqual([s['after']['selection']['value'] for s in selected], ['Lia', 'Hugo', ''])

    def test_accessible_combobox_name_and_unicode_value(self):
        html = '<label for="customer">Cidade</label><select id="customer"><option>Paris</option><option>São Paulo</option></select>'
        result = self.run_site(html, [select('São Paulo', {'role': 'combobox', 'name': 'Cidade', 'exact': True})])
        self.assertEqual(result['status'], 'passed', result)

    def test_invalid_control_and_options_fail_without_dispatch(self):
        cases = [
            ('<div id="customer" role="combobox">Lia</div>', 'native_select_required'),
            ('<select id="customer" multiple><option>Lia</option></select>', 'native_select_required'),
            ('<select id="customer" disabled><option>Lia</option></select>', 'option_disabled'),
            ('<fieldset disabled><select id="customer"><option>Lia</option></select></fieldset>', 'option_disabled'),
            ('<select id="customer"><option>Hugo</option></select>', 'option_not_unique'),
            ('<select id="customer"><option>Lia</option><option>Lia</option></select>', 'option_not_unique'),
            ('<select id="customer"><option disabled>Lia</option></select>', 'option_disabled'),
            ('<select id="customer"><optgroup disabled label="Group"><option>Lia</option></optgroup></select>', 'option_disabled'),
            ('<select id="customer" hidden><option>Lia</option></select>', 'select_failed'),
            ('<select id="customer"><option>Lia</option></select>' * 2, 'selector_not_unique'),
            ('<select id="customer">' + '<option>Lia</option>' * 1001 + '</select>', 'option_not_unique'),
        ]
        for html, error in cases:
            with self.subTest(html=html):
                result = self.run_site(html, [select('Lia')])
                self.assertEqual(result['status'], 'failed', result)
                self.assertEqual(result['steps'][0]['errorCode'], error, result)

    def test_page_cannot_spoof_selected_value_observation(self):
        html = HTML + '''<script>Object.defineProperty(HTMLSelectElement.prototype,'value', {get(){return 'Lia'},set(){}});
customer.onchange=()=>{customer.selectedIndex=0;};</script>'''
        # Prototype overrides in the author realm must not supply receipt data.
        result = self.run_site(html, [select('Lia')])
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['steps'][0]['errorCode'], 'selection_mismatch', result)

    def test_download_stays_blocked(self):
        html = '<button id="download" onclick="let a=document.createElement(\'a\');a.href=window.URL.createObjectURL(new Blob([\'csv\']));a.download=\'sales.csv\';a.click()">Download</button>'
        result = self.run_site(html, [step('click', '#download')])
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('download', result['blockedRequests'])


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS') == '1', 'real Chromium opt in')
class SelectBrowserTests(SelectionCases, unittest.TestCase):
    run_site = harness.BrowserTests.check


@unittest.skipUnless(os.environ.get('ODS_INSPECTION_TEST_IMAGE'), 'isolated Docker opt in')
class SelectDockerTests(SelectionCases, unittest.TestCase):
    run_site = harness.DockerCapsuleTests.invoke


if __name__ == '__main__':
    unittest.main()
