"""Opt-in paired Portal qualification, using the same current model and inputs.

Run baseline/inline/batch in alternating order, retaining every trial. No model
setting is changed and no failed trial is silently retried. Local auth only.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('--install-root', type=Path, required=True)
parser.add_argument('--workspace', type=Path, required=True)
parser.add_argument('--evidence', type=Path, required=True)
parser.add_argument('--mode', choices=['baseline', 'inline', 'batch', 'unavailable'], required=True)
args = parser.parse_args()
os.umask(0o077)
spec = importlib.util.spec_from_file_location('portal_verify', args.install_root / 'installers/verify-portal-api.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
port, key = verify.read_settings(str(args.install_root))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), verify.NoRedirect())
headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
run_id = 'laya-batch-qa-' + uuid.uuid4().hex
relative = 'Playground/' + run_id
work = args.workspace / relative
work.mkdir(parents=True, mode=0o700)
evidence = args.evidence / run_id
evidence.mkdir(parents=True, mode=0o700)
cases = json.loads((Path(__file__).parent / 'laya_batch_cases.json').read_text(encoding='utf-8'))
with (work / 'tickets.csv').open('w', encoding='utf-8', newline='') as output:
    writer = csv.DictWriter(output, fieldnames=['id', 'text'])
    writer.writeheader()
    writer.writerows({k: row[k] for k in ['id', 'text']} for row in cases)
method = {
    'baseline': 'Classifique com seu próprio raciocínio, sem usar Laya.',
    'inline': 'Use Laya pela ferramenta pixel_ods_laya para classificar os textos. Não use pixel_ods_laya_batch.',
    'batch': 'Use a extensão Laya para processar o arquivo e salvar o relatório.',
    'unavailable': 'Use a extensão Laya para processar o arquivo e salvar o relatório.',
}[args.mode]
prompt = f'''{method}
O arquivo {relative}/tickets.csv tem 32 chamados em inglês e português; colunas id e text.
Classifique cada chamado pelo assunto principal: billing (cobranças, pagamentos, reembolsos e faturas),
technical (falhas no funcionamento de recursos existentes), feature (pedidos de novas funcionalidades),
other (outros assuntos). Preserve todos os IDs e a ordem original. Salve um CSV com as colunas id e category
(colunas adicionais são permitidas) dentro de {relative}/results e confirme os bytes salvos.
Ao terminar, responda brevemente em português com o caminho do CSV e o número de linhas. Não publique site,
não pesquise na internet, não exclua arquivos. Não precisa escrever um relatório de texto adicional.'''
body = {'chat_id': run_id, 'request_id': run_id, 'messages': [{'role': 'user', 'content': prompt}]}
(evidence / 'request.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
with opener.open(urllib.request.Request(f'http://127.0.0.1:{port}/api/pixel/status', headers=headers), timeout=30) as response:
    runtime = json.load(response)
(evidence / 'runtime.json').write_text(json.dumps(runtime, indent=2), encoding='utf-8')
print(json.dumps({'started': run_id, 'mode': args.mode, 'evidence': str(evidence)}), flush=True)
started = time.monotonic()
completed = False
answer, errors, task = [], [], None
request = urllib.request.Request(f'http://127.0.0.1:{port}/api/pixel/chat/stream', data=json.dumps(body).encode(), headers=headers, method='POST')
with opener.open(request, timeout=300) as response, (evidence / 'events.jsonl').open('w', encoding='utf-8') as output:
    total = 0
    for raw in response:
        total += len(raw)
        if total > 8_000_000:
            raise RuntimeError('Oversized stream')
        line = raw.decode('utf-8').strip()
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if payload == '[DONE]':
            completed = True
            break
        event = json.loads(payload)
        output.write(json.dumps(event, ensure_ascii=False) + '\n')
        output.flush()
        if event.get('error'):
            errors.append(event['error'])
        if event.get('pixel_task'):
            task = event['pixel_task']
        for choice in event.get('choices', []):
            content = choice.get('delta', {}).get('content')
            if isinstance(content, str):
                answer.append(content)
seconds = round(time.monotonic() - started, 3)
reports = list((work / 'results').rglob('*.csv')) if (work / 'results').exists() else []
quality = []
issues = []
for report in reports:
    with report.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    ordered = [row.get('id') for row in rows] == [row['id'] for row in cases]
    correct = sum(row.get('category') == expected['expected'] for row, expected in zip(rows, cases)) if ordered else 0
    quality.append({'path': str(report.relative_to(args.workspace)), 'rows': len(rows), 'ordered': ordered,
                    'correct': correct, 'expected': len(cases)})
    if args.mode == 'batch':
        receipt_path = report.with_name('decisions.json')
        if not receipt_path.exists():
            issues.append('The model did not deliver a batch-generated report.')
            continue
        receipt = json.loads(receipt_path.read_text())
        if receipt['source']['sha256'] != hashlib.sha256((work / 'tickets.csv').read_bytes()).hexdigest():
            issues.append('Source digest differs from the input dataset.')
        if [item['sourceId'] for item in receipt['items']] != [case['id'] for case in cases]:
            issues.append('Original decision evidence lost source order or IDs.')
        corrections = []
        for row, original in zip(rows, receipt['items']):
            decision = original['answers']['category']
            if row.get('category') != decision['choice']:
                corrections.append(row['id'])
                if row.get('category_confidence') or row.get('category_answer_confidence'):
                    issues.append('Corrected label retains the original classifier confidence: ' + row['id'])
        quality[-1]['modelCorrections'] = corrections
        quality[-1]['originalCorrect'] = sum(item['answers']['category']['choice'] == case['expected']
                                            for item, case in zip(receipt['items'], cases))
activities = (task or {}).get('events', [])
label = 'Classifying dataset' if args.mode in ('batch', 'unavailable') else 'Consulting Laya'
laya_calls = [event for event in activities if (event.get('display') or {}).get('label') == label]
expected_state = 'failed' if args.mode == 'unavailable' else 'completed'
if args.mode != 'baseline' and (len(laya_calls) != 1 or laya_calls[0]['state'] != expected_state):
    issues.append('Expected exactly one ' + expected_state + ' ' + label + ' call.')
if args.mode == 'unavailable':
    if list((work / 'results').rglob('decisions.json')):
        issues.append('Unavailable service unexpectedly produced decision evidence.')
    final_answer = ''.join(answer).lower()
    if 'laya' not in final_answer or not any(term in final_answer for term in
                                          ('falh', 'indispon', 'não foi possível', 'não consegui')):
        issues.append('Final response did not disclose the failed Laya consultation.')
if args.mode == 'baseline' and any((event.get('display') or {}).get('label') in
                                  ('Classifying dataset', 'Consulting Laya') for event in activities):
    issues.append('Baseline unexpectedly used Laya.')
result = {'id': run_id, 'mode': args.mode, 'seconds': seconds, 'completed': completed, 'errors': errors,
          'quality': quality, 'issues': issues, 'answer': ''.join(answer), 'task': task, 'model': runtime.get('runtime')}
(evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k not in ['task', 'answer']}, ensure_ascii=False), flush=True)
if not completed or errors or issues or len(quality) != 1 or not quality[0]['ordered'] or quality[0]['correct'] != len(cases):
    raise SystemExit(1)
