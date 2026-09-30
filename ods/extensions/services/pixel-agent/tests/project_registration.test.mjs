import test from 'node:test';
import assert from 'node:assert/strict';
import {registerProjectBuild} from '../plugin/project-registration.mjs';

test('project tool is absent without an installed service configuration', () => {
  let calls = 0;
  registerProjectBuild({registerTool: () => calls++});
  assert.equal(calls, 0);
});

test('only Portal Pixel sessions receive the configured capability', () => {
  let factory;
  registerProjectBuild({pluginConfig: {projectBuildSocket: '/run/project/control.sock'},
    on() {},
    registerTool: (create, options) => {factory = create; assert.deepEqual(options.names, ['pixel_ods_project_build']);}});
  assert.equal(factory({agentId: 'other', sessionKey: 'session'}), null);
  assert.equal(factory({agentId: 'pixel', sessionKey: 'unknown'}), null);
  assert.equal(factory({agentId: 'pixel', sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64)}).name,
    'pixel_ods_project_build');
});

test('installed capability guides the owner to managed builds without granting authority', () => {
  let hook;
  registerProjectBuild({pluginConfig: {projectBuildSocket: '/run/project/control.sock'},
    registerTool() {}, on(name, callback) {assert.equal(name, 'before_prompt_build'); hook = callback;}});
  assert.equal(hook({}, {agentId:'other'}), undefined);
  assert.equal(hook({}, {agentId:'pixel',sessionKey:'unknown'}), undefined);
  const result = hook({}, {agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)});
  assert.match(result.appendSystemContext, /pixel_ods_project_build/);
  assert.match(result.appendSystemContext, /tool_search\/tool_describe/);
  assert.match(result.appendSystemContext, /not a permission grant/);
  assert.match(result.appendSystemContext, /do not resubmit/);
});
