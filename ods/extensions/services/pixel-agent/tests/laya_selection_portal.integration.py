"""Natural-request selection study. One attempt per case/phase, no hidden retries."""
import argparse
import csv
import datetime
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
parser.add_argument('--sessions', type=Path, required=True)
parser.add_argument('--evidence', type=Path, required=True)
parser.add_argument('--plan', type=Path, required=True)
parser.add_argument('--fixture', type=Path, required=True)
parser.add_argument('--phase', required=True)
parser.add_argument('--cases', nargs='+', required=True)
args = parser.parse_args()
os.umask(0o077)
root = args.evidence.resolve()
fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
workspace = args.workspace.resolve()
install = args.install_root.resolve()
assert workspace.is_dir() and args.sessions.is_dir()
assert args.phase and Path(args.phase).name == args.phase and args.phase not in ('.', '..')
spec = importlib.util.spec_from_file_location('verify', install / 'installers/verify-portal-api.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
port, key = verify.read_settings(str(install))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), verify.NoRedirect())
headers = {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
plan = json.loads(args.plan.read_text(encoding='utf-8'))
assert set(args.cases) <= set(plan['cases'])
assert len(set(args.cases)) == len(args.cases)
assert all(Path(name).name == name and name not in ('.', '..') for name in args.cases)
assert all(plan['cases'][name]['expected'] in ('use', 'skip') for name in args.cases)
assert all(0 < plan['cases'][name].get('rows', 32) <= len(fixture['cases']) for name in args.cases)
# Plans and fixtures must contain only synthetic/public task data. Expected
# answers, if present, stay in this runner and never enter the model workspace.
for name in args.cases:
    evidence = root / args.phase / name
    evidence.mkdir(parents=True, mode=0o700)
    run = 'portal-choice-' + uuid.uuid4().hex
    directory = 'Playground/' + run
    folder = workspace / directory
    folder.mkdir(mode=0o700)
    case = plan['cases'][name]
    count = case.get('rows', 32)
    items = fixture['cases'][:count]
    with (folder / 'articles.csv').open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=['id','text'])
        writer.writeheader()
        writer.writerows({k:item[k] for k in ('id','text')} for item in items)
    with (folder / 'orders.csv').open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(['id','category','amount'])
        writer.writerows((f'row{i}', 'a' if i % 2 == 0 else 'b', str(i)) for i in range(1,33))
    (folder / 'styles.css').write_text('.card { color: white; background: white; padding: 16px; }\n')
    prompt = case['prompt'].replace('{directory}', directory)
    body = dict(chat_id=run, request_id=run, messages=[dict(role='user', content=prompt)])
    (evidence / 'request.json').write_text(json.dumps(body, indent=2))
    meta = dict(case=name, phase=args.phase, run=run, directory=directory,
        promptSha256=hashlib.sha256(prompt.encode()).hexdigest(),
        sourceSha256=hashlib.sha256((folder/'articles.csv').read_bytes()).hexdigest(),
        guideSha256=hashlib.sha256((install/'extensions/services/pixel-agent/plugin/laya-tool.mjs').read_bytes()).hexdigest(),
        hintSha256=hashlib.sha256((install/'extensions/services/pixel-agent/plugin/laya-runtime.mjs').read_bytes()).hexdigest(),
        expected=case['expected'], fixtureSha256=hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        planSha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(), startedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
    (evidence / 'meta.json').write_text(json.dumps(meta, indent=2))
    print(json.dumps(dict(case=name, phase=args.phase, run=run, state='started')), flush=True)
    started = time.time()
    answer, errors = [], []
    completed = False
    task = None
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/pixel/chat/stream',
                                        headers=headers, data=json.dumps(body).encode(), method='POST')
        with opener.open(request, timeout=360) as response, (evidence/'events.jsonl').open('w') as out:
            total = 0
            for raw in response:
                total += len(raw)
                assert total < 12_000_000
                line = raw.decode().strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    completed = True
                    break
                event = json.loads(data)
                out.write(json.dumps(event)+'\n')
                out.flush()
                if event.get('pixel_task'):
                    task = event['pixel_task']
                if event.get('error'):
                    errors.append(event['error'])
                for choice in event.get('choices', []):
                    content = choice.get('delta', {}).get('content')
                    if isinstance(content, str):
                        answer.append(content)
    except Exception as exc:
        errors.append(dict(type=type(exc).__name__, message=str(exc)))
    # Audit the exact synthetic session, never unrelated owner conversations.
    sessions = []
    for path in args.sessions.glob('*.jsonl'):
        if path.name.endswith('.trajectory.jsonl') or path.stat().st_mtime < started-2:
            continue
        text = path.read_text(errors='replace')
        if run in text:
            sessions.append((path, text))
    calls, native_results = [], []
    for path, text in sessions:
        for line in text.splitlines():
            message = json.loads(line).get('message', {})
            if message.get('role') == 'assistant':
                for content in message.get('content', []):
                    if isinstance(content, dict) and content.get('type') == 'toolCall':
                        calls.append(dict(id=content.get('id'), tool=content.get('name'), arguments=content.get('arguments', {})))
            elif message.get('role') == 'toolResult':
                native_results.append(message)
    (evidence/'calls.json').write_text(json.dumps(calls, indent=2))
    laya = []
    for call in calls:
        tool, supplied = call['tool'], call['arguments']
        if tool == 'tool_call':
            tool, supplied = supplied.get('id','').split(':')[-1], supplied.get('args', {})
        if tool in ('pixel_ods_laya', 'pixel_ods_laya_batch'):
            laya.append(dict(id=call['id'], tool=tool, arguments=supplied))
    batch_ids = {call['id'] for call in laya if call['tool'] == 'pixel_ods_laya_batch' and call['id']}
    verified_outputs = {}
    for message in native_results:
        if message.get('toolCallId') not in batch_ids or message.get('isError'):
            continue
        result = (message.get('details') or {}).get('result', message)
        details = result.get('details') or {}
        if result.get('isError') or details.get('kind') != 'laya-batch-report' or details.get('status') != 'completed' or not details.get('readbackVerified'):
            continue
        for output in details.get('outputs', []):
            verified_outputs[output['path']] = output['sha256']
    receipts = []
    for path in folder.rglob('decisions.json'):
        data = json.loads(path.read_text())
        if data.get('kind') == 'laya-dataset-decisions':
            receipts.append(dict(path=str(path.relative_to(workspace)), rows=len(data['items']),
                nativeReadbackVerified=verified_outputs.get(str(path.relative_to(workspace))) == hashlib.sha256(path.read_bytes()).hexdigest(),
                questions=data['questions'],
                idsPreserved=[item['sourceId'] for item in data['items']] == [item['id'] for item in items],
                sourceMatches=data['source']['sha256'] == meta['sourceSha256']))
    if not calls:
        activity = (task or {}).get('events', [])
        assert not any((v.get('display') or {}).get('label') in ('Classifying dataset','Consulting Laya') for v in activity), 'Missing native tool evidence'
    result = dict(**meta, seconds=round(time.time()-started,3), completed=completed,
        errors=errors, answer=''.join(answer), task=task, calls=calls, layaCalls=laya,
        sessions=[str(p) for p,_ in sessions], receipts=receipts,
        files=[str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()],
        artifactCompletion='requires independent saved-artifact and interaction audit')
    result['selectionPass'] = any(v['nativeReadbackVerified'] for v in receipts) if case['expected'] == 'use' else not laya
    result['planningPass'] = all(len(v['arguments'].get('questions', [])) == 1 for v in laya)
    result['terminalComplete'] = completed and not errors and (task or {}).get('state') == 'completed'
    (evidence/'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k:result[k] for k in ['case','phase','seconds','terminalComplete','selectionPass','planningPass']} |
                     dict(layaCalls=len(laya), totalCalls=len(calls), files=len(result['files']))), flush=True)
    if not completed:
        raise SystemExit('Observation ended without terminal completion; inspect the existing run before continuing.')
