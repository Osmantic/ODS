"""Bounded text input on native fields inside the isolated snapshot."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from test_preview_inspection_blocked import bundle, execute_real, protocol, broker


def fill(value='QA após compactação', selector='#task'):
    return {'action':'fill', 'locator':{'selector':selector}, 'value':value}


def text(value, selector='h1'):
    return {'action':'assert-text', 'locator':{'selector':selector}, 'expectedText':value}


class FillProtocol(unittest.TestCase):
    def test_bound_printable_synthetic_value_only(self):
        for value in ('', 'QA após compactação', 'é'*256):
            protocol.validate_request(bundle('<input>', steps=[fill(value)])['request'])
        for value in ('x'*257, 'x\ny', '\x00', '\u202e', 2, None):
            with self.subTest(value=repr(value)), self.assertRaises(protocol.Invalid):
                protocol.validate_request(bundle('<input>', steps=[fill(value)])['request'])

    def test_old_image_is_rejected_before_reading_snapshot_or_running(self):
        request=bundle('<input>', steps=[fill()])['request']
        config={'ownerUid':os.getuid(), 'docker':'/usr/bin/docker','transport':'local','imageId':'sha256:'+'a'*64}
        with patch.object(broker,'bounded_process',return_value=b'') as process, patch.object(broker,'snapshot_bundle') as snapshot:
            result=broker.inspect_request(request, config)
        self.assertEqual(result['errorCode'], 'unsupported_capability')
        self.assertEqual(process.call_count,1)
        snapshot.assert_not_called()

    def test_text_only_v1_image_is_not_advertised_as_number_capable(self):
        request=bundle('<input type="number">', steps=[fill('3')])['request']
        config={'ownerUid':os.getuid(), 'docker':'/usr/bin/docker','transport':'local','imageId':'sha256:'+'a'*64}
        with patch.object(broker,'bounded_process',return_value=b'native-text-fill-v1\n') as process, patch.object(broker,'snapshot_bundle') as snapshot:
            result=broker.inspect_request(request, config)
        self.assertEqual(result['errorCode'], 'unsupported_capability')
        self.assertEqual(process.call_count,1)
        snapshot.assert_not_called()


@unittest.skipUnless(os.environ.get('ODS_PREVIEW_BROWSER_TESTS')=='1' or os.environ.get('ODS_INSPECTION_TEST_IMAGE'), 'requires isolated real browser')
class RealFill(unittest.TestCase):
    def test_number_split_form_and_invalid_zero_use_real_handlers(self):
        html='''<input id="amount" type="number" value="160" min="0" step="0.01">
        <input id="people" type="number" value="4" min="1" step="1" required>
        <button id="calculate">Calcular</button><h1>40.00</h1><p id="error"></p><script>
        document.querySelector('#calculate').onclick=()=>{
          const amount=document.querySelector('#amount'), people=document.querySelector('#people');
          if(!amount.checkValidity()||!people.checkValidity()){
            document.querySelector('#error').textContent='Quantidade inválida'; return;
          }
          document.querySelector('h1').textContent=(Number(amount.value)/Number(people.value)).toFixed(2);
        };</script>'''
        result=execute_real(bundle(html,steps=[text('40.00'),fill('100','#amount'),fill('3','#people'),
            {'action':'click','locator':{'selector':'#calculate'}},text('33.33'),fill('0','#people'),
            {'action':'click','locator':{'selector':'#calculate'}},text('Quantidade inválida','#error')]))
        self.assertEqual(result['status'],'passed',result)
        numeric=result['steps'][5]['after']['input']['numeric']
        self.assertTrue(numeric['rangeUnderflow'])
        self.assertTrue(result['steps'][5]['after']['input']['matches'])

    def test_number_clear_and_constraints_are_observed_not_rejected(self):
        result=execute_real(bundle('<input id="task" type="number" value="834739" min="1" max="10" step="2" required>',
            steps=[fill(''),fill('0'),fill('11'),fill('2'),fill('3')]))
        self.assertEqual(result['status'],'passed',result)
        for index,flag in enumerate(('valueMissing','rangeUnderflow','rangeOverflow','stepMismatch')):
            self.assertTrue(result['steps'][index]['after']['input']['numeric'][flag])
        self.assertFalse(result['steps'][4]['after']['input']['numeric']['stepMismatch'])
        self.assertNotIn('834739',json.dumps(result))

    def test_number_nonfinite_locale_and_invalid_syntax_never_pass(self):
        for value in ('NaN','Infinity','-Infinity','1e999','1,5',' 3 ','+1','0x10','1.','--1'):
            with self.subTest(value=value):
                result=execute_real(bundle('<input id="task" type="number" value="834739">',steps=[fill(value)]))
                self.assertEqual(result['steps'][0]['errorCode'],'numeric_value_required',result)
                self.assertNotIn('834739',json.dumps(result))

    def test_numeric_sensitive_or_readonly_fields_remain_denied(self):
        for attributes,code in [('aria-label="Security code"','text_field_required'),
                                ('autocomplete="cc-number"','text_field_required'),
                                ('readonly','field_not_editable'),('disabled','field_not_editable')]:
            result=execute_real(bundle(f'<input id="task" type="number" value="834739" {attributes}>',steps=[fill('3')]))
            self.assertEqual(result['steps'][0]['errorCode'],code,result)
            self.assertNotIn('834739',json.dumps(result))

    def test_fill_add_complete_filter_uses_real_page_event_handlers(self):
        html='''<input id="task" aria-label="Tarefa"><button id="add">Adicionar</button><button id="done">Concluir</button>
        <select id="filter"><option value="all">Todas</option><option value="pending">Pendentes</option></select>
        <h1>0</h1><p id="list"></p><p id="events">0/0</p><script>
        let title='',done=false,inputs=0,changes=0;
        const task=document.querySelector('#task'),list=document.querySelector('#list');
        task.addEventListener('input',()=>{inputs++;document.querySelector('#events').textContent=inputs+'/'+changes});
        task.addEventListener('change',()=>{changes++;document.querySelector('#events').textContent=inputs+'/'+changes});
        function render(){document.querySelector('h1').textContent=title&&!done?'1':'0';list.textContent=(document.querySelector('#filter').value==='pending'&&done)?'Vazio':title}
        document.querySelector('#add').onclick=()=>{title=task.value;render()};document.querySelector('#done').onclick=()=>{done=true;render()};document.querySelector('#filter').onchange=render;
        </script>'''
        steps=[fill(),text('1/1','#events'),{'action':'click','locator':{'selector':'#add'}},text('1'),text('QA após compactação','#list'),
               {'action':'click','locator':{'selector':'#done'}},text('0'),{'action':'select-option','locator':{'selector':'#filter'},'value':'pending'},text('Vazio','#list')]
        result=execute_real(bundle(html,steps=steps))
        self.assertEqual(result['status'],'passed',result)
        self.assertEqual(result['steps'][0]['after']['input'],{'eligible':True,'disabled':False,'readOnly':False,'matches':True})

    @unittest.skipUnless(os.environ.get('ODS_INSPECTION_REACT_ROOT'), 'requires existing React UMD assets, no download')
    def test_real_react_controlled_number_updates_state(self):
        root=Path(os.environ['ODS_INSPECTION_REACT_ROOT'])
        react=(root/'react/umd/react.production.min.js').read_bytes()
        dom=(root/'react-dom/umd/react-dom.production.min.js').read_bytes()
        html="""<div id="root"></div><script src="react.js"></script><script src="react-dom.js"></script><script>
        function App(){const [value,setValue]=React.useState('160');const [saved,setSaved]=React.useState('40');
          return React.createElement('div',null,React.createElement('input',{id:'task',type:'number',value,onChange:e=>setValue(e.target.value)}),
            React.createElement('button',{id:'save',onClick:()=>setSaved(String(Number(value)/4))},'Calcular'),React.createElement('h1',null,saved));}
        ReactDOM.createRoot(document.querySelector('#root')).render(React.createElement(App));
        </script>"""
        result=execute_real(bundle(html,{'react.js':react,'react-dom.js':dom},steps=[fill('100'),
            {'action':'click','locator':{'selector':'#save'}},text('25'),fill('-1.25e2'),
            {'action':'click','locator':{'selector':'#save'}},text('-31.25')]))
        self.assertEqual(result['status'],'passed',result)

    @unittest.skipUnless(os.environ.get('ODS_INSPECTION_REACT_ROOT'), 'requires existing React UMD assets, no download')
    def test_real_react_controlled_input_updates_state_and_renders(self):
        root=Path(os.environ['ODS_INSPECTION_REACT_ROOT'])
        react=(root/'react/umd/react.production.min.js').read_bytes()
        dom=(root/'react-dom/umd/react-dom.production.min.js').read_bytes()
        html="""<div id="root"></div><script src="react.js"></script><script src="react-dom.js"></script><script>
        function App(){const [value,setValue]=React.useState(''); const [saved,setSaved]=React.useState('Nada');
          return React.createElement('div',null,React.createElement('input',{id:'task',value,onChange:e=>setValue(e.target.value)}),
            React.createElement('button',{id:'save',onClick:()=>setSaved(value)},'Adicionar'),React.createElement('h1',null,saved));}
        ReactDOM.createRoot(document.querySelector('#root')).render(React.createElement(App));
        </script>"""
        result=execute_real(bundle(html,{'react.js':react,'react-dom.js':dom},steps=[fill(),{'action':'click','locator':{'selector':'#save'}},text('QA após compactação')]))
        self.assertEqual(result['status'],'passed',result)

    def test_textarea_and_empty_clear_do_not_echo_original_value(self):
        result=execute_real(bundle('<textarea id="task">PRIVATE-OLD-TEXT</textarea><h1>Ready</h1>',steps=[fill('á'),fill('')]))
        self.assertEqual(result['status'],'passed',result)
        self.assertNotIn('PRIVATE-OLD-TEXT',json.dumps(result))

    def test_denied_types_sensitive_fields_disabled_and_ambiguous(self):
        cases=[('<input type="password" id="task" value="PRIVATE">','text_field_required'),
               ('<input type="file" id="task">','text_field_required'),
               ('<input type="email" id="task">','text_field_required'),
               ('<input id="task" autocomplete="current-password">','text_field_required'),
               ('<input id="task" name="api_key">','text_field_required'),
               ('<label for="task">Password</label><input id="task">','text_field_required'),
               ('<span id="s">Secret token</span><input id="task" aria-labelledby="s">','text_field_required'),
               ('<div id="task" contenteditable>PRIVATE</div>','text_field_required'),
               ('<input id="task" readonly>','field_not_editable'),
               ('<input id="task" disabled>','field_not_editable'),
               ('<input id="task" hidden>','field_not_editable'),
               ('<input id="task"><input id="task">','selector_not_unique')]
        for html,code in cases:
            with self.subTest(html=html):
                result=execute_real(bundle(html,steps=[fill()]))
                self.assertEqual(result['status'],'failed',result)
                self.assertEqual(result['steps'][0]['errorCode'],code,result)
                self.assertNotIn('PRIVATE',json.dumps(result))

    def test_input_handler_cannot_widen_network_or_submit_form(self):
        html='<input id="task" oninput="fetch(\'/outside?private-canary\')"><h1>Ready</h1>'
        result=execute_real(bundle(html,steps=[fill(),text('Ready')]))
        self.assertEqual(result['status'],'failed',result)
        self.assertNotIn('private-canary',json.dumps(result))
        self.assertTrue(result.get('errorCode')=='request_blocked' or 'network' in result.get('blockedRequests',[]), result)

    def test_page_rejecting_value_never_passes(self):
        result=execute_real(bundle('<input id="task" oninput="this.value=\'\'">',steps=[fill()]))
        self.assertEqual(result['status'],'failed',result)
        self.assertEqual(result['steps'][0]['errorCode'],'fill_failed',result)


if __name__ == '__main__':
    unittest.main()
