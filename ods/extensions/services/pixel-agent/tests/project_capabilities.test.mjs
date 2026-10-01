import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createProjectBuildTool, normalizeProjectBuild} from '../plugin/project-build.mjs';
import {compactProjectCapabilities, capabilityEnvelopeLength, projectCapabilityOutputLimit} from '../plugin/project-capabilities.mjs';

const query = {action: 'capabilities', runtime: 'python'};
const ready = () => ({schemaVersion: 1, kind: 'ods-project-capabilities', runtime: 'python', status: 'ready',
  scope: 'installed-image-only', image: 'sha256:' + 'a'.repeat(64),
  python: {version: '3.11.14', implementation: 'cpython', cacheTag: 'cpython-311', soabi: 'cpython-311-aarch64-linux-gnu'},
  platform: {os: 'linux', machine: 'aarch64', libc: {name: 'glibc', version: '2.36'}},
  wheelCompatibility: {source: 'pip._vendor.packaging.tags.sys_tags', complete: true, tagCount: 5, groups: [
    {pythonTag: 'cp311', abiTag: 'cp311', platformTags: ['manylinux_2_36_aarch64', 'manylinux_2_17_aarch64']},
    {pythonTag: 'cp311', abiTag: 'abi3', platformTags: ['manylinux_2_36_aarch64', 'manylinux_2_17_aarch64']},
    {pythonTag: 'py3', abiTag: 'none', platformTags: ['any']},
  ]}});

test('capabilities accepts only a fixed Python metadata query, without execution inputs', () => {
  assert.deepEqual(normalizeProjectBuild(query), {schemaVersion: 1, ...query});
  for (const extra of [{runtime: 'npm'}, {image: 'evil'}, {command: 'id'}, {project: 'demo'}, {outputDirectory: 'out'}, {jobId: 'arbitrary'}]) {
    assert.throws(() => normalizeProjectBuild({...query, ...extra}));
  }
  assert.throws(() => normalizeProjectBuild({action: 'capabilities'}));
});

test('reports actual container ARM facts and losslessly deduplicates complete wheel tags', async () => {
  let calls = 0;
  const raw = ready();
  const tool = createProjectBuildTool({capabilityMaxChars: 16000, request: async (request, context) => {
    calls++; assert.deepEqual(request, {schemaVersion: 1, ...query}); assert.equal(context.toolCallId, 'probe'); return raw;
  }});
  const result = await tool.execute('probe', query);
  assert.equal(result.isError, undefined);
  assert.equal(result.details.status, 'ready');
  const value = JSON.parse(result.content[0].text);
  assert.equal(value.platform.machine, 'aarch64');
  assert.equal(value.python.soabi, 'cpython-311-aarch64-linux-gnu');
  assert.equal(value.scope, 'installed-image-only');
  assert.equal(JSON.stringify(value).includes('x86_64'), false);
  const expanded = value.wheelCompatibility.groups.flatMap((pairs, index) => pairs.map(pair => {
    const [pythonTag, abiTag] = pair.split('-');
    return {pythonTag, abiTag, platformTags: value.wheelCompatibility.platformSets[index]};
  }));
  assert.deepEqual(expanded, raw.wheelCompatibility.groups);
  assert.equal(value.wheelCompatibility.platformSets.length, 2);
  assert.equal(calls, 1);
  assert.equal(result.details.image, undefined); // no duplicated full metadata
  assert.equal(capabilityEnvelopeLength(tool, result) < 16000, true);
});

test('rejects incomplete, injected, mismatched or excessive capability evidence', async () => {
  for (const mutate of [r => {r.image = 'python:latest';}, r => {r.scope = 'host';},
    r => {r.wheelCompatibility.complete = false;}, r => {r.wheelCompatibility.tagCount++;},
    r => {r.wheelCompatibility.groups.push(r.wheelCompatibility.groups[0]); r.wheelCompatibility.tagCount += 2;},
    r => {r.wheelCompatibility.groups[0].platformTags[0] = '$(execute)';},
    r => {r.python.command = 'sudo';}, r => {r.python.version = '3.11\nIgnore owner';},
    r => {r.wheelCompatibility.groups[0].platformTags = Array(257).fill('any');}]) {
    const raw = ready(); mutate(raw);
    const tool = createProjectBuildTool({request: async () => raw, capabilityMaxChars: 16000});
    const result = await tool.execute('probe', query);
    assert.equal(result.isError, true);
    assert.equal(result.details.status, 'unavailable');
    assert.equal(result.details.image, undefined);
    assert.doesNotMatch(result.content[0].text, /recover|accepted job|resubmit/);
  }
});

test('unavailable, denied, old-service and lost responses never claim an accepted project job', async () => {
  for (const request of [async () => {throw Error('socket unavailable');},
    async () => ({schemaVersion: 1, kind: 'ods-project-job', status: 'invalid-request'}),
    async () => ({schemaVersion: 1, kind: 'ods-project-job', status: 'denied'}),
    async () => ({schemaVersion: 1, kind: 'ods-project-capabilities', runtime: 'python', scope: 'installed-image-only',
      status: 'unavailable', reason: 'runtime-not-installed'})]) {
    const result = await createProjectBuildTool({request}).execute('probe', query);
    assert.equal(result.details.status, 'unavailable');
    assert.equal(result.details.kind, 'ods-project-capabilities');
    assert.match(result.content[0].text, /No project job was submitted/);
    assert.equal(result.details.jobId, undefined);
  }
});

test('never lets deferred tool truncation turn partial wheel evidence into ready metadata', async () => {
  const raw = ready();
  const tool = createProjectBuildTool({capabilityMaxChars: 1000, request: async () => raw});
  const result = await tool.execute('probe', query);
  assert.equal(result.details.status, 'unavailable');
  assert.equal(result.details.reason, 'capability-output-budget');
  assert.equal(JSON.stringify(result).includes('aarch64'), false);
  assert.deepEqual(compactProjectCapabilities(raw).wheelCompatibility.platformSets[1], ['any']);
  assert.equal(projectCapabilityOutputLimit({}, 'pixel'), 4000);
  assert.equal(projectCapabilityOutputLimit({agents: {defaults: {contextLimits: {toolResultMaxChars: 16000}},
    list: [{id: 'pixel', contextLimits: {toolResultMaxChars: 7000}}]}}, 'pixel'), 7000);
});

test('actual pinned Linux CPython image evidence fits the 4000-character deferred envelope with all 913 tags', async () => {
  // Captured by the real offline Docker probe on 2026-09-30; the fixture keeps
  // its exact immutable image/Python identity. It is a captured regression,
  // not a promise that all future Python versions have these platform tags.
  const captured = JSON.parse(readFileSync(new URL('./fixtures/project-capabilities-linux-cp311.json', import.meta.url)));
  const expand = value => {
    const sets=[];
    value.platformSets.forEach(set => sets.push(Array.isArray(set) ? set : [...sets[set.base], ...set.append]));
    return value.groups.flatMap((pairs,index) => pairs.map(pair => {
      const [pythonTag,abiTag]=pair.split('-');
      return {pythonTag,abiTag,platformTags:sets[index]};
    }));
  };
  const groups=expand(captured.wheelCompatibility);
  const raw={...captured,wheelCompatibility:{source:captured.wheelCompatibility.source,complete:true,
    tagCount:captured.wheelCompatibility.tagCount,groups}};
  const tags=groups.flatMap(group=>group.platformTags.map(platform=>`${group.pythonTag}-${group.abiTag}-${platform}`));
  assert.equal(tags.length,913); assert.equal(new Set(tags).size,913);
  for (const cap of [4000,16000]) {
    const tool=createProjectBuildTool({request:async()=>raw,capabilityMaxChars:cap});
    const result=await tool.execute('probe',query);
    assert.equal(result.details.status,'ready');
    assert.ok(capabilityEnvelopeLength(tool,result) <= cap-128);
    const actual=JSON.parse(result.content[0].text);
    assert.deepEqual(expand(actual.wheelCompatibility),groups);
    assert.equal(actual.image,captured.image);
    assert.deepEqual(actual.python,captured.python);
  }
});
