import path from 'node:path';
import {normalizeReadonlyExec} from './readonly-execution-tool.mjs';

const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value) &&
  [Object.prototype,null].includes(Object.getPrototypeOf(value));
const within = (parent, child) => child === parent || child.startsWith(parent + '/');
const quote = value => "'" + value.replace(/'/g, "'\"'\"'") + "'";

function absolute(value) {
  return typeof value === 'string' && value !== '/' && path.posix.isAbsolute(value) &&
    path.posix.normalize(value) === value && !value.endsWith('/') && !/[\\\x00-\x1f\x7f]/.test(value);
}

// This copy belongs to one calculation. It never changes the installed config
// or broadens the owner's tool allow/deny, approval or exec-security policy.
export function buildReadonlyExecutionConfig(config, {workspaceRoot,privateRoot,nonce,imageId} = {}) {
  if (!plain(config) || !absolute(workspaceRoot) || !absolute(privateRoot) ||
      within(workspaceRoot,privateRoot) || within(privateRoot,workspaceRoot) ||
      !/^[0-9a-f]{32}$/.test(nonce ?? '') || !/^sha256:[0-9a-f]{64}$/.test(imageId ?? ''))
    throw new Error('readonly-config-invalid');
  const pixels = config.agents?.list?.filter(agent => agent?.id === 'pixel');
  if (pixels?.length !== 1 || (pixels[0].workspace ?? config.agents?.defaults?.workspace) !== workspaceRoot)
    throw new Error('readonly-config-scope-mismatch');
  const copy = structuredClone(config);
  const pixel = copy.agents.list.find(agent => agent.id === 'pixel');
  const containerPrefix = 'ods-pixel-ro-' + nonce.slice(0,16) + '-';
  const sandbox = {
    mode:'all', backend:'docker', scope:'session', workspaceAccess:'ro',
    workspaceRoot:privateRoot + '/sandboxes',
    docker:{image:imageId,containerPrefix,workdir:'/workspace',network:'none',
      user:'sandbox',readOnlyRoot:true,capDrop:['ALL'],
      tmpfs:['/tmp:rw,nosuid,nodev,size=16m','/run:rw,nosuid,nodev,size=4m'],
      memory:'512m',memorySwap:'512m',cpus:1,pidsLimit:128,env:{LANG:'C.UTF-8'}},
    browser:{enabled:false},prune:{idleHours:0,maxAgeDays:0},
  };
  copy.agents.defaults ??= {};
  copy.agents.defaults.sandbox = structuredClone(sandbox);
  pixel.sandbox = sandbox;
  for (const owner of [copy,pixel]) {
    owner.tools ??= {};
    owner.tools.exec = {...owner.tools.exec,host:'sandbox'};
    owner.tools.elevated = {...owner.tools.elevated,enabled:false};
  }
  pixel.skills = [];
  return {config:copy,sandboxSessionKey:'agent:pixel:ods-readonly:' + nonce,containerPrefix};
}

export function readonlyShellCommand(request) {
  const normalized = normalizeReadonlyExec(request);
  const directory = normalized.workdir === '.' ? '/agent' : '/agent/' + normalized.workdir;
  // An actual newline keeps comments, heredocs and quoting in model code intact.
  return 'cd -- ' + quote(directory) + ' || exit $?\n' + normalized.command;
}
