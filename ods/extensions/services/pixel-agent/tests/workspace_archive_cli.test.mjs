import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync,readdirSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {WORKSPACE_ARTIFACT_DELIVERY_GUIDANCE as guidance,createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';

const python=process.platform==='win32'?'python':'python3';
function fixture(t) {
  const root=mkdtempSync(path.join(tmpdir(),'ods-archive-cli-'));
  t.after(()=>{
    assert.equal(path.dirname(root),path.resolve(tmpdir()));
    assert.ok(path.basename(root).startsWith('ods-archive-cli-'));
    rmSync(root,{recursive:true,force:true});
  });
  return {root,run(args){
    const r=spawnSync(python,args,{cwd:root,encoding:'utf8',timeout:30000});
    assert.ifError(r.error);return r;
  }};
}

test('the actual handoff commands preserve nested source bytes without extraction',t=>{
  const {root,run}=fixture(t);
  const commands=[...guidance.matchAll(/`python3 ([^`]+)`/g)].map(m=>m[1].split(' '));
  assert.equal(commands.length,3,'guidance must expose create, list and integrity commands');
  mkdirSync(path.join(root,'project','src'),{recursive:true});
  const source={'project/src/main.py':'print("café")\n','project/README.md':'# Example\r\n','project/test_main.py':'import unittest\n'};
  for(const [name,text] of Object.entries(source))writeFileSync(path.join(root,name),text);
  writeFileSync(path.join(root,'unrelated.txt'),'not requested');
  assert.equal(run(commands[0]).status,0);
  const listed=run(commands[1]),checked=run(commands[2]);
  assert.equal(listed.status,0);assert.equal(checked.status,0);
  assert.doesNotMatch(checked.stdout,/corrupted/i);
  for(const name of Object.keys(source))assert.ok(listed.stdout.includes(name),name);
  assert.ok(statSync(path.join(root,'out.zip')).size<4*1024*1024);
  const bytes=run(['-c','import zipfile,json; z=zipfile.ZipFile("out.zip"); print(json.dumps({n:z.read(n).hex() for n in z.namelist() if not n.endswith("/")}))']);
  assert.equal(bytes.status,0,bytes.stderr);
  assert.deepEqual(JSON.parse(bytes.stdout),Object.fromEntries(Object.entries(source).map(([n,s])=>[n,Buffer.from(s).toString('hex')])));
  for(const [name,text] of Object.entries(source))assert.deepEqual(readFileSync(path.join(root,name)),Buffer.from(text));
  assert.deepEqual(readdirSync(root).sort(),['out.zip','project','unrelated.txt']);
});

test('ZIP CLI success alone cannot establish complete contents or integrity',t=>{
  const {run}=fixture(t);
  const missing=run(['-m','zipfile','-c','missing.zip','missing.py']);
  assert.equal(missing.status,0,'CPython silently skips missing source operands');
  const empty=run(['-c','import zipfile,json; print(json.dumps(zipfile.ZipFile("missing.zip").namelist()))']);
  assert.deepEqual(JSON.parse(empty.stdout),[]);
  const corrupt=run(['-c','import zipfile,pathlib; p=pathlib.Path("corrupt.zip"); z=zipfile.ZipFile(p,"w",compression=zipfile.ZIP_STORED); z.writestr("main.py",b"unique-payload"); z.close(); b=p.read_bytes(); i=b.index(b"unique-payload"); p.write_bytes(b[:i]+b"X"+b[i+1:])']);
  assert.equal(corrupt.status,0,corrupt.stderr);
  const checked=run(['-m','zipfile','-t','corrupt.zip']);
  assert.equal(checked.status,0,'CRC failure is reported in output even with exit zero');
  assert.match(checked.stdout,/main\.py.*corrupt|corrupt.*main\.py/i);
  assert.match(guidance,/exit 0 alone is insufficient/i);
});

test('registered guidance and rejected source-file calls share the same archive recovery',async()=>{
  const tool=createWorkspaceArtifactTool({},{});
  assert.match(tool.description,/python3 -m zipfile -c project-download\.zip project-dir/);
  const failure=await tool.execute('rejected',{relativePath:'main.py'});
  assert.equal(failure.details.code,'invalid-arguments');
  assert.match(failure.content[0].text,/python3 -m zipfile -t project-download\.zip/);
  assert.match(failure.content[0].text,/No extraction or recursive cleanup is needed/);
  assert.equal(failure.details.file,undefined);
});
