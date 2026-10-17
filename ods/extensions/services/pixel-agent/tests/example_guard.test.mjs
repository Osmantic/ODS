// The shipped example's guard (plugin/example-guard.mjs) driven through the
// real tool-loop guard hooks the plugin entry calls: before_prompt_build
// (observeRun, observeExampleRun, exampleContextForRun), before_tool_call,
// after_tool_call, tool_result_persist, before_agent_finalize and agent_end.
// The reply the owner reads is the one Portal's ingress delivers: the run's
// verification as the gateway serves it, parsed and applied by the ingress's
// own functions (host/pixel_ingress.mjs), and over real loopback HTTP at the
// end of this file. Each workspace holds the real shipped game files and the
// installers' record in a temporary OPENCLAW_STATE_DIR, and every model
// command really runs in bash (Git Bash on Windows) in that workspace.
// example_guard_wiring.test.mjs runs the same hooks through the plugin
// entry's own registration code.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import {createRequire, syncBuiltinESMExports} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import {fileURLToPath} from 'node:url';

import {applyVerificationToCompletion, createIngressServer, parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {exampleRecordDirectory, exampleRecordFile} from '../plugin/example-projects.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {createExampleGuard, EXAMPLE_NOTES, exampleGuardTesting, mentionsExample, namesExample} from '../plugin/example-guard.mjs';
import {createPendingClarifications} from '../plugin/pending-clarification.mjs';
import {requestsNewPlaygroundProject} from '../plugin/playground-projects.mjs';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const SHIPPED = fileURLToPath(new URL('../example-projects/elf-of-doom/', import.meta.url));
// Every temporary folder this file makes, removed when it ends.
const temporary = [];
const scratch = prefix => { const folder = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix))); temporary.push(folder); return folder; };
after(() => { for (const folder of temporary) fs.rmSync(folder, {recursive: true, force: true}); });
// Portal's state folder: outside every workspace, as it is outside the sandbox.
process.env.OPENCLAW_STATE_DIR = scratch('ods-example-guard-state-');
fs.mkdirSync(exampleRecordDirectory(), {mode: 0o700});
// Git Bash on Windows, never the WSL launcher that a bare "bash" can resolve to.
const BASH = process.platform === 'win32'
  ? path.join(process.env.ProgramFiles ?? 'C:\\Program Files', 'Git', 'bin', 'bash.exe') : 'bash';
const NO_BASH = process.platform === 'win32' && !fs.existsSync(BASH) ? 'needs Git Bash' : false;
const POSIX_ONLY = process.platform === 'win32' ? 'POSIX permissions' : false;
// File links need a privilege Windows grants only in developer mode.
const FILE_LINKS = (() => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-guard-link-'));
  try { fs.symlinkSync('target', path.join(folder, 'link'), 'file'); return true; }
  catch (error) { if (error.code === 'EPERM') return false; throw error; }
  finally { fs.rmSync(folder, {recursive: true, force: true}); }
})();
const DIRECTORY_LINK = process.platform === 'win32' ? 'junction' : 'dir';
// Whether this disk ignores case, as Windows and macOS disks normally do.
const IGNORES_CASE = fs.existsSync(SHIPPED.replace(/elf-of-doom/g, 'ELF-OF-DOOM'));

// ------------------------------------------------------------- the rule --

// Only the documented form at the very start of the owner's message unlocks
// a change: "In elf-of-doom," / "In elf-of-doom:" / "elf-of-doom:" /
// "In Playground/elf-of-doom,", in any case, with hyphens, spaces or
// underscores between the words.
const UNLOCKS = [
  'In elf-of-doom, make it harder',
  'in ELF OF DOOM: add a boss',
  'In elf-of-doom, make the arrows give 7',
  'In Elf of Doom, make the arrows give 7.',
  'IN ELF-OF-DOOM, make it red',
  'elf-of-doom: add a pause menu',
  'Elf_of_Doom: slow the brutes down',
  'In Playground/elf-of-doom, fix the jump bug',
  'In elf-of-doom: add sound',
  '  "In elf-of-doom, keep the score but make the arrows faster"',
  '\u201cIn elf-of-doom, add a pause button\u201d',
  "'in elf_of_doom, make it harder'",
  'In elf of doom, build a new game mode with a boss',
  'In elf-of-doom, delete this game',
  '/goal In elf-of-doom, add three more realms',
  'In elf-of-doom,make it harder',
];
const STAYS_LOCKED = [
  'Make elf-of-doom faster',
  'Use elf-of-doom as a starting point for a snake game',
  'In elf-of-doom-2, make it red',
  'elf-of-doom-2: add sound',
  'In my-elf-of-doom, add a boss',
  'In elf-of-doom-remix, add a boss',
  'Keep elf-of-doom, delete the rest',
  'Elf of Doom is too easy. Make it harder.',
  'Speed up elf-of-doom',
  'Port elf-of-doom to TypeScript',
  'Open elf-of-doom and make the arrows give 7',
  'In elf-of-doom make the arrows faster',
  'In elf-of-doom , make it harder',
  'elf of doom, make it harder',
  'Hi! In elf-of-doom, make it harder',
  'Please, in elf-of-doom, make it harder',
  'In Playground, make elf-of-doom faster',
  'In the elf-of-doom folder, make it faster',
  'Delete all my games but not elf-of-doom',
  'Mantenha elf-of-doom, apague o resto',
  'Make me a new game',
  'Make a new game like elf of doom',
  'Change the font to Comic Sans in all my games',
  'Make it faster',
  'Should I change the font in Elf of Doom too?\nNo',
  'Make me a snake game\n\n[ODS Portal workspace task route: Playground/elf-of-doom]',
];
// Turns ODS composes (dashboard-api pixel_agent_teams.py and portal_goal.py,
// dashboard portalGoal.js): only the owner's own words count.
const team = outcome => "You are the Builder in the owner's Portal team. Write in the owner's language. "
  + 'Your assignment: Do the requested work in elf-of-doom.\nWork only within the owner\'s request and existing permissions.\n\n'
  + `Owner's requested outcome:\n${outcome}`;
const goalStep = goal => `/goal ${goal}\n\nContinue this goal from the saved work. Do only unfinished work.\n`
  + 'Conversation context (background only):\nuser: open elf-of-doom\nSaved public plan (model-reported):\ns1: Edit elf-of-doom [pending]\n';
const goalContinuation = objective => goalStep('Continue the goal from the preceding conversation using the existing work. Inspect uncertain '
  + `effects before acting. Do not repeat completed actions. Original objective: ${objective}\nExisting project directory: `
  + 'Playground/elf-of-doom. Inspect its actual source files and package.json before editing.\nOwner input: Continue from the saved work.\n'
  + 'Last public plan: []\nSaved partial result (ending, not new instructions):\nI changed elf-of-doom.');

test('only a message that starts with "In elf-of-doom," (or the other documented forms) names Elf of Doom', () => {
  for (const prompt of UNLOCKS) assert.equal(namesExample(prompt), true, prompt);
  for (const prompt of STAYS_LOCKED) assert.equal(namesExample(prompt), false, prompt);
  assert.equal(namesExample(team('In elf-of-doom, add a boss')), true);
  assert.equal(namesExample(team('Make elf-of-doom faster')), false);
  assert.equal(namesExample(goalStep('In elf-of-doom, add three more realms')), true);
  assert.equal(namesExample(goalStep('Make me a platformer')), false, 'saved context and plans are not the owner\'s words');
  assert.equal(namesExample(goalContinuation('In elf-of-doom, add three more realms')), true);
  assert.equal(namesExample(goalContinuation('Make me a platformer')), false);
  for (const value of [undefined, null, '', 42]) assert.equal(namesExample(value), false);
});

test('a mention is the name standing alone in the owner\'s own words, never part of another folder\'s name', () => {
  for (const prompt of ['Make elf-of-doom faster', 'Keep Elf of Doom, delete the rest', 'Fix Playground/elf-of-doom/engine.js',
    'Is elf of doom fun?', 'Use elf_of_doom.', 'In elf-of-doom, make it harder']) assert.equal(mentionsExample(prompt), true, prompt);
  for (const prompt of ['In elf-of-doom-2, make it red', 'Fix my-elf-of-doom', 'Rename elf-of-doom_old', 'Make it faster',
    'Make me a snake game\n\n[ODS Portal workspace task route: Playground/elf-of-doom]', team('Make the snake faster'), undefined]) {
    assert.equal(mentionsExample(prompt), false, prompt);
  }
});

// ------------------------------------------------------------- fixtures --

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
// Every entry below folder, without following links: kind, mode, link count
// and bytes.
function tree(folder, {links = true, skip = () => false} = {}) {
  const entries = {};
  const walk = relative => {
    if (skip(relative)) return;
    const target = path.join(folder, relative), stat = fs.lstatSync(target), key = relative || '.';
    if (stat.isSymbolicLink()) entries[key] = 'link';
    else if (stat.isDirectory()) {
      entries[key] = `dir ${(stat.mode & 0o777).toString(8)}`;
      for (const name of fs.readdirSync(target).sort()) walk(relative ? `${relative}/${name}` : name);
    } else entries[key] = `file ${(stat.mode & 0o777).toString(8)}${links ? ` ${stat.nlink}` : ''} ${sha256(fs.readFileSync(target))}`;
  };
  if (fs.lstatSync(folder, {throwIfNoEntry: false})) walk('');
  return entries;
}
const listing = root => fs.readdirSync(path.join(root, 'Playground')).sort();
// The whole workspace except the example, plus any folder outside it. The
// Playground folder's own entry is left out: a missing one is the only thing
// outside the example the guard creates, to hold it (its contents count).
// On a disk that ignores case, any spelling of the example's name is the
// example. Link counts are left out: breaking a hard link into the example
// lowers the outside file's count.
function outsideExample(root, others = []) {
  const example = new RegExp(String.raw`^Playground/elf-of-doom(?:/|$)`, IGNORES_CASE ? 'i' : '');
  const all = Object.entries(tree(root, {links: false, skip: key => example.test(key)})).filter(([key]) => key !== 'Playground');
  return {workspace: Object.fromEntries(all), others: others.map(folder => tree(folder, {links: false}))};
}

// A workspace holding the real shipped game, the installers' record (by
// default saying ODS added it) and two of the owner's own games.
function workspace({record = 'added', root = scratch('ods-example-guard-')} = {}) {
  fs.cpSync(SHIPPED, path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  for (const game of ['snake-game', 'space-race']) {
    const folder = path.join(root, 'Playground', game);
    fs.mkdirSync(folder, {recursive: true});
    fs.writeFileSync(path.join(folder, 'index.html'), `<!doctype html><title>${game}</title><link rel="stylesheet" href="style.css">\n`);
    fs.writeFileSync(path.join(folder, 'style.css'), 'body { font-family: Georgia, serif; }\n');
    fs.writeFileSync(path.join(folder, 'game.js'), 'const speed = 1;\n');
  }
  if (record) {
    fs.writeFileSync(exampleRecordFile(root), `${JSON.stringify({schemaVersion: 1, workspace: root, received: ['elf-of-doom'],
      added: record === 'added' ? ['elf-of-doom'] : []})}\n`, {mode: 0o600});
  }
  return {root, example: path.join(root, 'Playground', 'elf-of-doom'), original: tree(path.join(root, 'Playground', 'elf-of-doom'))};
}
const outsideFolder = () => scratch('ods-example-guard-outside-');
const posix = value => value.replaceAll('\\', '/');
const escaped = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// One model command, run for real in the workspace.
function shell(root, command, env = {}) {
  const result = spawnSync(BASH, ['-c', `export PATH="/usr/bin:$PATH"; ${command}`], {cwd: root, encoding: 'utf8',
    env: {...process.env, WS: posix(root), ...Object.fromEntries(Object.entries(env).map(([key, value]) => [key, posix(value)]))}});
  assert.equal(result.status, 0, `${command}\n${result.stderr}`);
  return result.stdout;
}

// The reply Portal receives for a run whose model wrote text: the run's
// verification as the gateway's /pixel-ods/verification route serves it
// (JSON), parsed and applied by the ingress.
function delivered(guard, runId, text) {
  const served = JSON.parse(JSON.stringify(guard.deliveryVerificationForRun(runId)));
  return deliver(text, served, runId);
}
function deliver(text, served, runId = 'run') {
  const completion = {id: runId, object: 'chat.completion', choices: [{index: 0, message: {role: 'assistant', content: text}, finish_reason: 'stop'}]};
  return applyVerificationToCompletion(completion, parseVerificationResponse(served, runId)).choices[0].message.content;
}
const count = (text, part) => text.split(part).length - 1;

const OWNER = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const chat = letter => `agent:pixel:openai-user:ods-${letter.repeat(64)}`;
let runs = 0;
// One owner turn through the plugin's hooks. Every tool call asserts that the
// guard changed nothing outside Playground/elf-of-doom (nor in others).
function turn(guard, prompt, root, {sessionKey = OWNER, runId = `run-${++runs}`, trigger = 'user', others = [], env = {}, ...rest} = {}) {
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey, trigger, ...rest};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost: 'sandbox', workspaceRoot: root});
  guard.observeExampleRun(context);
  let calls = 0;
  // A call's hooks: before_tool_call, the call itself, after_tool_call and
  // tool_result_persist. start() returns the remaining steps, so a test can
  // act while the call is still running.
  const start = (toolName, params) => {
    const toolCallId = `${runId}-call-${++calls}`, ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, runId, params, toolCallId}, ctx, 'pixel');
    assert.notEqual(before?.block, true, before?.blockReason);
    const used = before?.params ?? params;
    return {params: used, end(output = 'ok', details = {status: 'completed', exitCode: 0}) {
      const afterTool = outsideExample(root, others);
      const result = {content: [{type: 'text', text: output || 'ok'}], details};
      guard.afterToolCall({toolName, runId, params: used, toolCallId, result}, ctx, 'pixel');
      const message = {role: 'toolResult', toolName, toolCallId, ...structuredClone(result)};
      const persisted = guard.toolResultPersist({toolName, toolCallId, message}, ctx, 'pixel')?.message ?? message;
      assert.deepEqual(outsideExample(root, others), afterTool, 'the guard touches nothing outside Playground/elf-of-doom');
      return {params: used, text: persisted.content.map(block => block.text).join('\n')};
    }};
  };
  const call = (toolName, params, run) => { const started = start(toolName, params); return started.end(run(started.params)); };
  return {
    context,
    guidance: guard.exampleContextForRun(runId),
    start,
    exec: command => call('exec', {command, workdir: '/workspace'}, () => shell(root, command, env)),
    write: (file, content) => call('write', {path: file, content}, used => {
      const target = path.join(root, ...used.path.split('/'));
      fs.mkdirSync(path.dirname(target), {recursive: true});
      fs.writeFileSync(target, content);
      return `Successfully wrote ${used.path}`;
    }),
    read: file => call('read', {path: file}, used => fs.readFileSync(path.join(root, ...used.path.split('/')), 'utf8')),
    edit: (file, oldText, newText) => call('edit', {path: file, oldText, newText}, used => {
      const target = path.join(root, ...used.path.split('/'));
      fs.writeFileSync(target, fs.readFileSync(target, 'utf8').replace(oldText, newText));
      return `Successfully replaced text in ${used.path}.`;
    }),
    // The run's finalize and end, then the reply as Portal receives it: the
    // ingress reads the verification after the run ended.
    finish(text = 'Done.') {
      guard.beforeAgentFinalize({runId, lastAssistantMessage: text}, context, 'pixel');
      guard.observeAgentEnd({runId, success: true}, context);
      return delivered(guard, runId, text);
    },
  };
}
const RESTORED = EXAMPLE_NOTES.restored;
const SED_FONT = `sed -i 's/font-family:[^;]*/font-family: "Comic Sans MS"/'`;
const ENGINE_EDIT = "sed -i 's/const /let /' Playground/elf-of-doom/engine.js";

// ------------------------------------------------------ non-naming turns --

const BULK = [
  `${SED_FONT} $(grep -rl font-family "$WS/Playground")`,
  `for f in $(find Playground -name '*.css'); do ${SED_FONT} "$f"; done`,
  `find Playground -name '*.css' | while read f; do ${SED_FONT} "$f"; done`,
  `node -e "const fs=require('fs');for(const d of fs.readdirSync('Playground')){const f='Playground/'+d+'/style.css';if(fs.existsSync(f))fs.writeFileSync(f,'body{font-family:Comic Sans MS}')}"`,
];
for (const command of BULK) {
  test(`a turn that does not name it gets the game back after the call and the run: ${command}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const run = turn(createToolLoopGuard(), 'Change the font to Comic Sans in all my games', root);
    assert.equal(run.guidance, undefined, 'a message that does not mention it gets no guidance');
    const {text} = run.exec(command);
    assert.deepEqual(tree(example), original);
    assert.match(fs.readFileSync(path.join(root, 'Playground', 'snake-game', 'style.css'), 'utf8'), /Comic Sans MS/,
      'the owner\'s other games changed as asked');
    assert.ok(text.endsWith(RESTORED.model), text);
    const reply = run.finish('I changed the font in all your games.');
    assert.equal(reply, `I changed the font in all your games.\n\n${RESTORED.owner}`);
    assert.doesNotMatch(reply, /discord/i, 'a restore is not a failure');
    assert.deepEqual(tree(example), original);
  });
}

test('deleting every game brings the example back and leaves the others deleted', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Delete my old games', root);
  run.exec('rm -r Playground/*');
  assert.deepEqual(tree(example), original);
  assert.deepEqual(listing(root), ['elf-of-doom']);
  run.finish();
  assert.deepEqual(tree(example), original);
});

test('deleting Playground itself brings back a Playground holding only the example', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Delete all my projects', root);
  run.exec('rm -r Playground');
  assert.deepEqual(listing(root), ['elf-of-doom']);
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a folder moved away comes back, and the moved copy is left alone', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Tidy up my Playground', root);
  run.exec('mv Playground/elf-of-doom Playground/old');
  assert.deepEqual(tree(example), original);
  assert.deepEqual(tree(path.join(root, 'Playground', 'old')), original);
  run.finish();
});

test('a case-only rename of the folder is undone', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Capitalize my project names', root);
  run.exec('mv Playground/elf-of-doom Playground/tmp-name && mv Playground/tmp-name Playground/Elf-Of-Doom');
  assert.ok(listing(root).includes('elf-of-doom'));
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a folder replaced by a link becomes the real folder again, and the link\'s target is untouched', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  // An identical copy elsewhere: following the link would see nothing changed.
  const outside = outsideFolder();
  fs.cpSync(example, path.join(outside, 'copy'), {recursive: true});
  const before = tree(outside);
  const run = turn(createToolLoopGuard(), 'Clean up my projects', root, {others: [outside], env: {OUT: path.join(outside, 'copy')}});
  run.exec(`rm -r Playground/elf-of-doom && node -e "require('fs').symlinkSync(process.env.OUT, 'Playground/elf-of-doom', '${DIRECTORY_LINK}')"`);
  assert.equal(fs.lstatSync(example).isSymbolicLink(), false);
  assert.deepEqual(tree(example), original);
  assert.deepEqual(tree(outside), before);
  run.finish();
});

test('a new-game request writes into a new folder, and a write that lands in the example is put back', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const routed = turn(guard, 'Make me a new game', root, {sessionKey: chat('b')});
  const {params, text} = routed.write('Playground/elf-of-doom/index.html', '<!doctype html><title>New game</title>\n');
  assert.equal(params.path, 'Playground/elf-of-doom-2/index.html', 'project routing picks a new folder');
  assert.doesNotMatch(text, /\[ODS Portal\] ODS put/);
  assert.deepEqual(tree(example), original);
  // After reading the example, routing keeps the path; the guard puts it back.
  const read = turn(guard, 'Make me a new game', root, {sessionKey: chat('c')});
  read.read('Playground/elf-of-doom/index.html');
  const landed = read.write('Playground/elf-of-doom/index.html', '<!doctype html><title>New game</title>\n');
  assert.equal(landed.params.path, 'Playground/elf-of-doom/index.html');
  assert.ok(landed.text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.match(read.finish('Your new game is ready.'), new RegExp(`${escaped(RESTORED.owner)}$`));
});

test('a turn about a sibling folder such as elf-of-doom-2 leaves the example guarded', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  fs.cpSync(example, path.join(root, 'Playground', 'elf-of-doom-2'), {recursive: true});
  const guard = createToolLoopGuard();
  const sibling = turn(guard, 'In elf-of-doom-2, make the arrows give 7', root);
  assert.equal(sibling.guidance, undefined, 'elf-of-doom-2 is not a mention of the example');
  assert.ok(sibling.exec("sed -i 's/const /let /' Playground/elf-of-doom*/engine.js").text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.match(fs.readFileSync(path.join(root, 'Playground', 'elf-of-doom-2', 'engine.js'), 'utf8'), /\blet /);
  assert.ok(sibling.finish('The arrows give 7 now.').endsWith(RESTORED.owner));
  const mistaken = turn(guard, 'Make elf-of-doom-2 faster', root);
  mistaken.read('Playground/elf-of-doom/engine.js');
  assert.ok(mistaken.write('Playground/elf-of-doom/engine.js', 'const speed = 99;\n').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  mistaken.finish();
});

// ------------------------------------- mentions that do not name it --

// Each of these mentions the example without the documented form: the model
// is told before it starts, and whatever the command does to the example is
// put back while the rest of the command's work stays.
const MENTIONS = [
  ['Keep elf-of-doom as it is. Change the font to Comic Sans in all my other games.', `${SED_FONT} Playground/*/style.css`, 'snake-game/style.css'],
  ['Keep Elf of Doom, delete the rest of my games', 'rm -r Playground/*', null],
  ['Delete all my games but not elf-of-doom', 'rm -r Playground/*', null],
  ['Mantenha elf-of-doom, apague o resto', 'rm -r Playground/*', null],
  ['Change the font in all my games without touching elf-of-doom', `${SED_FONT} Playground/*/style.css`, 'snake-game/style.css'],
  ['Elf of Doom is fine, leave it. Make the snake faster.', "sed -i 's/speed = 1/speed = 2/' Playground/*/game.js && sed -i 's/const /let /' Playground/*/*.js", 'snake-game/game.js'],
  ['Take the music from elf-of-doom and add it to the snake game', 'mv Playground/elf-of-doom/engine.js Playground/snake-game/music.js', 'snake-game/music.js'],
  ['Make a new game like elf of doom', 'mkdir -p Playground/elf-like && cp -r Playground/elf-of-doom/* Playground/elf-like/ && echo "<p>new</p>" >> Playground/elf-of-doom/index.html', 'elf-like/index.html'],
  ['Use elf-of-doom as a starting point for a snake game', `printf '<!doctype html><title>Snake</title>\\n' > Playground/elf-of-doom/index.html`, null],
  ['Make elf-of-doom faster', ENGINE_EDIT, null],
  ['Elf of Doom is too easy. Make it harder.', ENGINE_EDIT, null],
  ['Speed up elf-of-doom', ENGINE_EDIT, null],
  ['Port elf-of-doom to TypeScript', `${ENGINE_EDIT} && echo '// typed' > Playground/elf-of-doom/types.ts`, null],
  ['Open elf-of-doom and make the arrows give 7', ENGINE_EDIT, null],
];
for (const [prompt, command, kept] of MENTIONS) {
  test(`a turn that mentions it without the documented form gets guidance and the game back: ${prompt}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const before = listing(root);
    const run = turn(createToolLoopGuard(), prompt, root);
    assert.equal(run.guidance, EXAMPLE_NOTES.guidance);
    // A new project's first step is a file of its own.
    if (prompt.startsWith('Make a new game')) run.write('Playground/elf-like/notes.txt', 'like elf of doom\n');
    assert.ok(run.exec(command).text.endsWith(RESTORED.model));
    assert.deepEqual(tree(example), original);
    if (kept) assert.ok(fs.existsSync(path.join(root, 'Playground', ...kept.split('/'))), `${kept} stays as the command left it`);
    else if (command.startsWith('rm')) assert.deepEqual(listing(root), ['elf-of-doom']);
    else assert.deepEqual(listing(root), before);
    assert.ok(run.finish().endsWith(RESTORED.owner));
  });
}

// --------------------------------------------------------- named changes --

for (const prompt of ['In elf-of-doom, make the arrows give 7', 'in ELF OF DOOM: add a boss']) {
  test(`a turn that starts with the documented form changes it, and the change sticks: ${prompt}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const guard = createToolLoopGuard();
    const run = turn(guard, prompt, root);
    assert.equal(run.guidance, undefined);
    const {text} = run.exec(`${ENGINE_EDIT} && echo '// boss' > Playground/elf-of-doom/boss.js`);
    const changed = tree(example);
    assert.notDeepEqual(changed, original);
    assert.doesNotMatch(text, /\[ODS Portal\]/);
    assert.equal(run.finish('Done.'), 'Done.', 'no notice');
    assert.deepEqual(tree(example), changed);
    // The next turn that does not name it starts from the changed game.
    const later = turn(guard, 'Make the snake faster', root);
    later.read('Playground/snake-game/game.js');
    assert.equal(later.finish('Done.'), 'Done.');
    assert.deepEqual(tree(example), changed);
  });
}

test('nothing carries over: after a named change, "Make it faster" is put back to that change', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const first = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  first.exec(ENGINE_EDIT);
  first.finish();
  const named = tree(example);
  assert.notDeepEqual(named, original);
  const next = turn(guard, 'Make it faster', root);
  assert.ok(next.exec("sed -i 's/let /var /' Playground/elf-of-doom/engine.js").text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), named);
  assert.ok(next.finish().endsWith(RESTORED.owner));
});

// Wordings that ask for a new project on their own. After the documented
// form they are changes to the example, made in its own folder.
const NEW_PROJECT_WORDING = ['make a game over screen', 'make a mobile version of the game', 'build a new game mode with a boss',
  'write a test script', 'create a game manual', 'create an app icon'];

for (const wording of NEW_PROJECT_WORDING) {
  test(`"In elf-of-doom, ${wording}" works in Playground/elf-of-doom itself, never a new folder`, {skip: NO_BASH}, () => {
    assert.equal(requestsNewPlaygroundProject(wording), true, 'on its own it asks for a new project');
    assert.equal(requestsNewPlaygroundProject(`In elf-of-doom, ${wording}`), false);
    const {root, example} = workspace();
    const run = turn(createToolLoopGuard(), `In elf-of-doom, ${wording}`, root);
    run.read('Playground/elf-of-doom/index.html');
    const page = run.edit('Playground/elf-of-doom/index.html', '</body>', '<script src="extra.js"></script></body>');
    assert.equal(page.params.path, 'Playground/elf-of-doom/index.html');
    assert.equal(run.write('Playground/elf-of-doom/extra.js', '// extra\n').params.path, 'Playground/elf-of-doom/extra.js');
    assert.equal(run.edit('Playground/elf-of-doom/engine.js', 'const ', 'let ').params.path, 'Playground/elf-of-doom/engine.js');
    const check = run.start('exec', {command: 'node --check engine.js', workdir: '/workspace/Playground/elf-of-doom'});
    assert.equal(check.params.workdir, '/workspace/Playground/elf-of-doom');
    check.end();
    assert.doesNotMatch(run.exec("sed -i 's/var /let /' Playground/elf-of-doom/engine.js").text, /\[ODS Portal\]/);
    assert.doesNotMatch(run.finish('Done.'), /ODS put|could not put/);
    assert.deepEqual(listing(root), ['elf-of-doom', 'snake-game', 'space-race'], 'no new folder');
    assert.match(fs.readFileSync(path.join(example, 'index.html'), 'utf8'), /<script src="extra\.js"><\/script><\/body>/);
    assert.equal(fs.readFileSync(path.join(example, 'extra.js'), 'utf8'), '// extra\n');
  });
}

test('a chat whose earlier new game went to elf-of-doom-2 still changes the real game on "In elf-of-doom,"', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const first = turn(guard, 'Make me a new game', root, {sessionKey: chat('7')});
  assert.equal(first.write('Playground/elf-of-doom/index.html', '<!doctype html><title>New game</title>\n').params.path,
    'Playground/elf-of-doom-2/index.html');
  first.finish('Your new game is ready.');
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root, {sessionKey: chat('7')});
  assert.equal(named.read('Playground/elf-of-doom/engine.js').params.path, 'Playground/elf-of-doom/engine.js');
  assert.equal(named.edit('Playground/elf-of-doom/engine.js', 'const ', 'let ').params.path, 'Playground/elf-of-doom/engine.js');
  named.exec("sed -i 's/const /let /' Playground/elf-of-doom/engine.js");
  assert.equal(named.finish('The arrows give 7 now.'), 'The arrows give 7 now.');
  assert.doesNotMatch(fs.readFileSync(path.join(example, 'engine.js'), 'utf8'), /\bconst /);
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground', 'elf-of-doom-2')), ['index.html']);
});

// ---------------------------------------------- clarification answers --

const QUESTION = [{id: 'scope', question: 'Should I change the font in Elf of Doom too?', options: ['Yes', 'No']}];
const HARDER = [{id: 'what', question: 'What should be harder?', options: ['More enemies', 'Faster enemies']}];
// A card the asking run recorded for OWNER's chat (pending-clarification.mjs),
// then the owner's answer turn: the question, a newline and the answer.
function answer(request, questions, reply, root) {
  const pendingClarifications = createPendingClarifications({stateDir: path.join(scratch('ods-example-guard-cards-'), 'cards')});
  assert.equal(pendingClarifications.remember({sessionKey: OWNER, runId: 'asking-run', questions, request}), true);
  const guard = createToolLoopGuard({pendingClarifications});
  return turn(guard, `${questions[0].question}\n${reply}`, root);
}

test('an answer to a clarification card is named by the request it answers, never by the model\'s question', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const declined = answer('Change the font in all my games', QUESTION, 'No', root);
  assert.equal(declined.guidance, EXAMPLE_NOTES.guidance, 'the question mentions it, so the model is told');
  assert.ok(declined.exec(`${SED_FONT} Playground/*/style.css`).text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.ok(declined.finish().endsWith(RESTORED.owner));
  const accepted = answer('Change the font in all my games', QUESTION, 'Yes', root);
  accepted.exec(`${SED_FONT} Playground/*/style.css`);
  assert.deepEqual(tree(example), original, 'a yes to the model\'s question does not name it either');
  accepted.finish();
  const named = answer('In elf-of-doom, make it harder', HARDER, 'More enemies', root);
  assert.equal(named.guidance, undefined);
  assert.doesNotMatch(named.exec(ENGINE_EDIT).text, /\[ODS Portal\]/);
  assert.equal(named.finish('There are more enemies now.'), 'There are more enemies now.');
  assert.notDeepEqual(tree(example), original);
});

test('an answer to a card about a turn that mentions it gets guidance too', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = answer('Make elf-of-doom harder', HARDER, 'More enemies', root);
  assert.equal(run.guidance, EXAMPLE_NOTES.guidance, 'the request the card answers mentions it');
  assert.ok(run.exec(ENGINE_EDIT).text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.ok(run.finish().endsWith(RESTORED.owner));
});

test('an answer to a card of a turn that names it works in the real folder, even when the answer reads like a new project', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const run = answer('In elf-of-doom, make it harder', HARDER, 'Create a new game mode with a boss', root);
  assert.equal(requestsNewPlaygroundProject(`${HARDER[0].question}\nCreate a new game mode with a boss`), true);
  assert.equal(run.write('Playground/elf-of-doom/boss.js', '// boss\n').params.path, 'Playground/elf-of-doom/boss.js');
  assert.doesNotMatch(run.finish('There is a boss mode now.'), /ODS put|could not put/);
  assert.equal(fs.readFileSync(path.join(example, 'boss.js'), 'utf8'), '// boss\n');
  assert.deepEqual(listing(root), ['elf-of-doom', 'snake-game', 'space-race']);
});

// ------------------------------------------- the known-good tree --

test('a change made while Portal is idle is the owner\'s: the next run keeps it and puts back only what it changes itself', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  assert.equal(turn(guard, 'Make the snake faster', root).finish('Done.'), 'Done.');
  // The owner edits the game by hand (or a background process an earlier
  // run started writes after that run ended).
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// my own change\n');
  fs.rmSync(path.join(example, 'legacy.js'));
  const mine = tree(example);
  const next = turn(guard, 'Change the font in all my games', root);
  assert.deepEqual(tree(example), mine, 'a run never restores when it starts');
  assert.ok(next.exec(`${SED_FONT} Playground/*/style.css`).text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), mine, 'put back to the owner\'s tree, not the shipped one');
  assert.equal(next.finish('Done.'), `Done.\n\n${RESTORED.owner}`);
  assert.equal(turn(guard, 'What can you do?', root).finish('I can build games.'), 'I can build games.');
  assert.deepEqual(tree(example), mine);
});

test('a folder the owner deletes by hand stays deleted: no later run brings it back or says anything', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  turn(guard, 'Make the snake faster', root).finish();
  fs.rmSync(example, {recursive: true});
  for (const prompt of ['Delete my snake game', 'What can you do?']) {
    const run = turn(guard, prompt, root);
    assert.equal(run.guidance, undefined);
    run.read('Playground/space-race/game.js');
    if (prompt.startsWith('Delete')) run.exec('rm -r Playground/snake-game');
    assert.equal(run.finish('Done.'), 'Done.');
  }
  assert.deepEqual(listing(root), ['space-race']);
});

test('a run that starts while another works keeps the known-good tree, so the first run\'s change is still put back', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const first = turn(guard, 'Reformat all my games', root, {sessionKey: chat('b')});
  const reformat = first.start('exec', {command: 'reformat', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// formatted\n');
  const second = turn(guard, 'What can you do?', root, {sessionKey: chat('c')});
  assert.deepEqual(second.guidance, undefined);
  assert.ok(reformat.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.equal(second.finish('I can build games.'), 'I can build games.');
  assert.ok(first.finish('Reformatted.').endsWith(RESTORED.owner));
});

test('after a gateway restart the folder as ODS first finds it is the known-good tree', () => {
  const {root, example} = workspace();
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// before the restart\n');
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root);
  const found = tree(example);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// during the run\n');
  assert.ok(run.finish('Done.').endsWith(RESTORED.owner));
  assert.deepEqual(tree(example), found);
});

test('a change made outside any tool call is put back at finalize, so the reply can say so before the run ends', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make the snake faster', root);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// late\n');
  guard.beforeAgentFinalize({runId: run.context.runId, lastAssistantMessage: 'The snake is faster.'}, run.context, 'pixel');
  assert.deepEqual(tree(example), original);
  assert.equal(guard.deliveryVerificationForRun(run.context.runId).notice, RESTORED.owner);
  assert.ok(run.finish('The snake is faster.').endsWith(RESTORED.owner));
});

test('a run that ends without finalizing (stopped or failed) is still checked when it ends', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make the snake faster', root);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// late\n');
  guard.observeAgentEnd({runId: run.context.runId, success: false, error: 'aborted'}, run.context);
  assert.deepEqual(tree(example), original);
  assert.equal(guard.exampleNoticeForRun(run.context.runId), RESTORED.owner);
});

test('a later attempt of a run that does not name it gets that run\'s earlier changes put back', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make the snake faster', root);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// first attempt\n');
  // The runtime retries the attempt: before_prompt_build runs again.
  guard.observeExampleRun(run.context);
  assert.deepEqual(tree(example), original);
  assert.equal(run.finish('Done.'), `Done.\n\n${RESTORED.owner}`);
});

// ------------------------------------------------- what Portal delivers --

test('a reply ODS replaces with its own text is followed by the notice, once', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  // The owner asked for a download that never arrives: ODS's failure text replaces the reply.
  const run = turn(guard, 'Make my snake game project downloadable as a zip archive', root);
  run.exec("sed -i 's/const /let /' Playground/*/*.js");
  assert.deepEqual(tree(example), original);
  const reply = run.finish('Here is your download.');
  const verification = guard.deliveryVerificationForRun(run.context.runId);
  assert.equal(verification.status, 'failed');
  assert.match(verification.text, /^The requested download was not attached/);
  assert.equal(verification.notice, RESTORED.owner);
  assert.equal(reply, `${verification.text}\n\n${RESTORED.owner}`);
});

// The host's preview receipt digest (workspace-preview.mjs) for one file.
function previewReceipt(relativeDirectory, content) {
  const name = Buffer.from('index.html'), bytes = Buffer.from(content);
  const nameLength = Buffer.alloc(4), contentLength = Buffer.alloc(8);
  nameLength.writeUInt32BE(name.length); contentLength.writeBigUInt64BE(BigInt(bytes.length));
  const digest = createHash('sha256').update(nameLength).update(name).update(contentLength).update(bytes).digest('hex');
  const siteId = `site-${digest.slice(0, 24)}`;
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory, siteId, port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`, files: 1, bytes: bytes.length, sha256: digest, entryFile: 'index.html',
    entrySha256: sha256(bytes), httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
}

// A new game with a verified preview, during which a stray change to the
// example was put back.
function previewTurn(runId = undefined) {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make me a new space game', root, {sessionKey: chat('d'), ...(runId ? {runId} : {})});
  const content = '<!doctype html><title>Space game</title>';
  const {params} = run.write('Playground/space-game/index.html', content);
  const stray = run.start('read', {path: params.path});
  fs.appendFileSync(path.join(example, 'ui.js'), 'x\n');
  assert.ok(stray.end(content).text.endsWith(RESTORED.model));
  const preview = run.start('pixel_ods_workspace_preview', {relativeDirectory: path.posix.dirname(params.path)});
  preview.end('Published.', previewReceipt(preview.params.relativeDirectory, content));
  assert.deepEqual(tree(example), original);
  return {guard, run};
}

test('a new game\'s verified preview reply keeps the model\'s text and the evidence, then the notice, once', () => {
  const {guard, run} = previewTurn();
  const reply = run.finish('Your new space game is ready.');
  assert.equal(guard.deliveryVerificationForRun(run.context.runId).deliveryMode, 'append');
  assert.ok(reply.startsWith('Your new space game is ready.\n\n'), reply);
  assert.match(reply, /\nPublication scope: [^\n]+\n\nODS put Playground\/elf-of-doom back as it was\./);
  assert.ok(reply.endsWith(RESTORED.owner), reply);
  assert.equal(count(reply, RESTORED.owner), 1);
});

test('the ingress adds the notice once, and only one plain line that is not on a question card', () => {
  const runId = 'run-notice';
  assert.equal(deliver(`Done.\n\n${RESTORED.owner}`, {status: 'none', notice: RESTORED.owner}), `Done.\n\n${RESTORED.owner}`);
  assert.equal(deliver('', {status: 'failed', text: 'Stopped.', notice: RESTORED.owner}), `Stopped.\n\n${RESTORED.owner}`);
  for (const value of [{status: 'pending', text: 'Which one?', notice: RESTORED.owner}, {status: 'none', notice: ''},
    {status: 'none', notice: 'two\nlines'}, {status: 'none', notice: 'x'.repeat(1025)}, {status: 'none', notice: 7}]) {
    assert.throws(() => parseVerificationResponse(value, runId), /verification state unavailable/, JSON.stringify(value));
  }
});

test('a question card carries no notice', {skip: NO_BASH}, async () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Change the font in all my games', root);
  run.exec(`${SED_FONT} Playground/*/style.css`);
  assert.deepEqual(tree(example), original);
  const questions = [{id: 'font', question: 'Which font?', options: ['Comic Sans', 'Georgia']}];
  const result = await createAskUserTool().execute('ask', {questions});
  const ask = {...run.context, toolName: 'pixel_ods_ask_user', toolCallId: 'ask'};
  guard.beforeToolCall({toolName: 'pixel_ods_ask_user', params: {questions}, toolCallId: 'ask'}, ask, 'pixel');
  guard.afterToolCall({toolName: 'pixel_ods_ask_user', toolCallId: 'ask', params: {questions}, result}, ask, 'pixel');
  guard.beforeAgentFinalize({runId: run.context.runId, lastAssistantMessage: 'Which font?'}, run.context, 'pixel');
  const verification = guard.deliveryVerificationForRun(run.context.runId);
  assert.equal(verification.status, 'pending');
  assert.equal(Object.hasOwn(verification, 'notice'), false);
  assert.doesNotMatch(delivered(guard, run.context.runId, 'Which font?'), /ODS put/);
});

// ------------------------------------------------------- restore safety --

test('a hard link to a file outside is replaced, and the outside file is never written', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root, {others: [outside], env: {OUT: outside}});
  run.exec('cp Playground/elf-of-doom/engine.js "$OUT/engine.js" && rm Playground/elf-of-doom/engine.js'
    + ' && ln "$OUT/engine.js" Playground/elf-of-doom/engine.js && echo changed >> Playground/elf-of-doom/engine.js');
  assert.deepEqual(tree(example), original);
  assert.match(fs.readFileSync(path.join(outside, 'engine.js'), 'utf8'), /changed\n$/, 'the outside file keeps what the command wrote');
  run.finish();
});

test('a hard link with the same bytes is broken, so a later write through the other name never reaches the game', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  const run = turn(createToolLoopGuard(), 'Back up my games', root, {others: [outside], env: {OUT: outside}});
  assert.ok(run.exec('ln Playground/elf-of-doom/engine.js "$OUT/engine.js"').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original, 'one name again');
  fs.appendFileSync(path.join(outside, 'engine.js'), '// later\n');
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a link to a file outside is removed, and its target is never written', {skip: NO_BASH || (!FILE_LINKS && 'file links need privilege here')}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  fs.writeFileSync(path.join(outside, 'target.txt'), 'outside\n');
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root, {others: [outside], env: {OUT: outside}});
  run.exec(`rm Playground/elf-of-doom/engine.js && node -e "require('fs').symlinkSync(process.env.OUT + '/target.txt', 'Playground/elf-of-doom/engine.js', 'file')"`);
  assert.deepEqual(tree(example), original);
  assert.equal(fs.readFileSync(path.join(outside, 'target.txt'), 'utf8'), 'outside\n');
  run.finish();
});

test('a link to a folder outside, added inside, is removed without touching that folder', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  fs.writeFileSync(path.join(outside, 'keep.txt'), 'keep\n');
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root, {others: [outside], env: {OUT: outside}});
  run.exec(`node -e "require('fs').symlinkSync(process.env.OUT, 'Playground/elf-of-doom/assets', '${DIRECTORY_LINK}')"`);
  assert.deepEqual(tree(example), original);
  assert.equal(fs.readFileSync(path.join(outside, 'keep.txt'), 'utf8'), 'keep\n');
  run.finish();
});

test('a file replaced by a folder of the same name is a file again', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Organize my code into folders', root);
  run.exec('rm Playground/elf-of-doom/engine.js && mkdir -p Playground/elf-of-doom/engine.js/parts && touch Playground/elf-of-doom/engine.js/parts/x.js');
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a same-size edit that keeps the file\'s time is still put back', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  const run = turn(createToolLoopGuard(), 'Tidy my code', root, {env: {OUT: outside}});
  const engine = path.join(example, 'engine.js');
  const before = fs.statSync(engine);
  const command = 'cp -p Playground/elf-of-doom/engine.js "$OUT/stamp" && sed -i \'s/const /CONST /\' Playground/elf-of-doom/engine.js'
    + ' && touch -r "$OUT/stamp" Playground/elf-of-doom/engine.js';
  const call = run.start('exec', {command, workdir: '/workspace'});
  shell(root, command, {OUT: outside});
  // Only the file's identity or change time shows the edit.
  const edited = fs.statSync(engine);
  assert.deepEqual([edited.size, edited.mtimeMs], [before.size, before.mtimeMs]);
  assert.notDeepEqual(tree(example), original);
  assert.ok(call.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a read-only file or folder the owner had stays read-only when it is put back', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  fs.chmodSync(path.join(example, 'ui.js'), 0o444);
  fs.chmodSync(example, 0o555);
  const original = tree(example);
  const run = turn(createToolLoopGuard(), 'Rewrite my games\' interfaces', root);
  run.exec('chmod u+w Playground/elf-of-doom Playground/elf-of-doom/ui.js && rm Playground/elf-of-doom/ui.js'
    + ' && echo "// new" > Playground/elf-of-doom/ui.js && echo "// added" > Playground/elf-of-doom/extra.js');
  assert.deepEqual(tree(example), original);
  run.finish();
  fs.chmodSync(example, 0o755);
});

test('modes are put back too: a read-only file, a mode-only change and a read-only folder', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Make all my projects read-only', root);
  // A file made read-only (and changed): Windows refuses to replace it as it is.
  const readOnly = run.start('exec', {command: 'chmod a-w Playground/elf-of-doom/engine.js', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'engine.js'), '// changed\n');
  fs.chmodSync(path.join(example, 'engine.js'), 0o444);
  assert.ok(readOnly.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  // Only a mode changes, no byte.
  const modeOnly = run.start('exec', {command: 'chmod a-w Playground/elf-of-doom/ui.js', workdir: '/workspace'});
  fs.chmodSync(path.join(example, 'ui.js'), 0o444);
  assert.ok(modeOnly.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  // The folder itself made read-only, with a file added.
  const folder = run.start('exec', {command: 'chmod a-w Playground/elf-of-doom', workdir: '/workspace'});
  fs.writeFileSync(path.join(example, 'LICENSE'), 'mine\n');
  fs.chmodSync(example, 0o555);
  assert.ok(folder.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  run.finish();
});

test('a folder made unreadable, or read-only with a folder added inside, is put back (POSIX)', {skip: NO_BASH || POSIX_ONLY}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Lock my projects', root);
  run.exec('chmod 000 Playground/elf-of-doom');
  assert.deepEqual(tree(example), original);
  run.exec('mkdir -p Playground/elf-of-doom/extra && cp Playground/snake-game/game.js Playground/elf-of-doom/extra/ && chmod -R a-w Playground');
  assert.deepEqual(tree(example), original);
  run.exec('chmod -R u+w Playground');
  run.finish();
});

test('when Playground is replaced during a run, the owner hears it with the help link, and later turns stay quiet', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  const run = turn(guard, 'Start over with an empty workspace', root);
  const {text} = run.exec('rm -r Playground && echo "not a folder" > Playground');
  assert.equal(fs.readFileSync(path.join(root, 'Playground'), 'utf8'), 'not a folder\n', 'ODS never replaces Playground itself');
  assert.equal(fs.existsSync(example), false);
  assert.ok(text.endsWith(EXAMPLE_NOTES.failed.model));
  assert.ok(run.finish('Your workspace is empty now.').endsWith(EXAMPLE_NOTES.failed.owner));
  assert.match(EXAMPLE_NOTES.failed.owner, /https:\/\/discord\.gg\/4ntNp9MAwC/);
  assert.equal(turn(guard, 'What can you do?', root).finish('I can build games.'), 'I can build games.');
  assert.equal(warnings.length, 2, warnings.join('\n'));
  assert.match(warnings[0], /could not put Playground\/elf-of-doom back.*link or not a folder/);
  assert.match(warnings[1], /is not guarding Playground\/elf-of-doom.*link or not a folder/);
});

test('a Playground linked away for one call is put back at a later call, once it is a real folder again', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const outside = outsideFolder();
  const run = turn(createToolLoopGuard(), 'Move my projects to a shared drive', root, {others: [outside], env: {OUT: outside}});
  const linked = run.exec(`mv Playground "$OUT/pg" && node -e "require('fs').symlinkSync(process.env.OUT + '/pg', 'Playground', '${DIRECTORY_LINK}')"`);
  assert.ok(linked.text.endsWith(EXAMPLE_NOTES.failed.model));
  assert.ok(run.exec('rm Playground && mv "$OUT/pg" Playground && rm Playground/elf-of-doom/*.js').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.ok(run.finish('Moved them back.').endsWith(RESTORED.owner));
});

test('a workspace replaced by a link during a run is never written through', () => {
  const {root, example} = workspace();
  const outside = outsideFolder();
  const examples = createExampleGuard();
  examples.begin('swapped', root, false);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// changed\n');
  fs.renameSync(root, `${root}-moved`);
  temporary.push(`${root}-moved`);
  fs.symlinkSync(outside, root, DIRECTORY_LINK);
  assert.equal(examples.check('swapped'), 'failed');
  assert.deepEqual(fs.readdirSync(outside), [], 'nothing is written where the link points');
  fs.rmSync(root, {recursive: true, force: true});
});

test('a workspace whose parent folder is swapped for a link during a run is never written through', () => {
  const parent = scratch('ods-example-guard-parent-');
  const {root, example} = workspace({root: path.join(parent, 'ws')});
  const examples = createExampleGuard();
  examples.begin('parent-swapped', root, false);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// changed\n');
  // Through the link, the same path names another real workspace.
  const outside = outsideFolder();
  fs.mkdirSync(path.join(outside, 'ws', 'Playground', 'elf-of-doom'), {recursive: true});
  fs.writeFileSync(path.join(outside, 'ws', 'Playground', 'elf-of-doom', 'ui.js'), 'outside\n');
  const before = tree(outside);
  fs.renameSync(parent, `${parent}-moved`);
  temporary.push(`${parent}-moved`);
  fs.symlinkSync(outside, parent, DIRECTORY_LINK);
  try {
    assert.equal(examples.check('parent-swapped'), 'failed');
    assert.deepEqual(tree(outside), before, 'nothing is written where the link points');
  } finally { fs.rmSync(parent, {recursive: true, force: true}); }
});

test('putting a file back never changes the read-only flag it shares with another name', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const shared = path.join(root, 'Playground', 'snake-game', 'game.js');
  fs.chmodSync(shared, 0o444);
  const run = turn(createToolLoopGuard(), 'Share the engine between my games', root);
  try {
    assert.ok(run.exec('rm Playground/elf-of-doom/engine.js && ln Playground/snake-game/game.js Playground/elf-of-doom/engine.js')
      .text.endsWith(RESTORED.model));
    assert.deepEqual(tree(example), original);
    assert.equal(fs.statSync(shared).mode & 0o222, 0, 'the other name is still read-only');
    assert.equal(fs.readFileSync(shared, 'utf8'), 'const speed = 1;\n');
    run.finish();
  } finally { fs.chmodSync(shared, 0o644); }
});

// ------------------------------------------------------------- whose copy --

for (const record of ['present', null]) {
  test(`the owner's own Playground/elf-of-doom is never guarded (record: ${record})`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace({record});
    const run = turn(createToolLoopGuard(), 'Change the font in elf-of-doom and all my other games', root);
    assert.equal(run.guidance, undefined);
    assert.doesNotMatch(run.exec(`${SED_FONT} Playground/*/style.css`).text, /\[ODS Portal\]/);
    assert.equal(run.finish('Done.'), 'Done.');
    assert.notDeepEqual(tree(example), original);
  });
}

test('a workspace given through a linked path is guarded at its real path', () => {
  const {root, example, original} = workspace();
  const alias = path.join(outsideFolder(), 'alias');
  fs.symlinkSync(root, alias, DIRECTORY_LINK);
  const run = turn(createToolLoopGuard(), 'Make the snake faster', alias);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// changed\n');
  assert.ok(run.finish().endsWith(RESTORED.owner));
  assert.deepEqual(tree(example), original);
});

test('a linked Playground is the owner\'s layout: no notice, and the gateway log says so once', () => {
  const {root, example} = workspace();
  const moved = path.join(outsideFolder(), 'Playground');
  fs.renameSync(path.join(root, 'Playground'), moved);
  fs.symlinkSync(moved, path.join(root, 'Playground'), DIRECTORY_LINK);
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  for (const prompt of ['Hello, what can you do?', 'Make the snake faster']) {
    const run = turn(guard, prompt, root);
    run.read('Playground/snake-game/style.css');
    fs.appendFileSync(path.join(example, 'ui.js'), '\n// the owner\'s change\n');
    assert.equal(run.finish('Done.'), 'Done.');
  }
  assert.equal(warnings.length, 1, warnings.join('\n'));
  assert.match(warnings[0], /is not guarding Playground\/elf-of-doom.*link or not a folder/);
});

test('a Playground the owner links elsewhere between turns is their layout: no notice, logged once', () => {
  const {root, example} = workspace();
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  assert.equal(turn(guard, 'Make the snake faster', root).finish('Done.'), 'Done.');
  const moved = path.join(outsideFolder(), 'Playground');
  fs.renameSync(path.join(root, 'Playground'), moved);
  fs.symlinkSync(moved, path.join(root, 'Playground'), DIRECTORY_LINK);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// the owner\'s change\n');
  for (const prompt of ['Hello, what can you do?', 'Make the snake slower']) {
    assert.equal(turn(guard, prompt, root).finish('Done.'), 'Done.');
  }
  assert.match(fs.readFileSync(path.join(moved, 'elf-of-doom', 'ui.js'), 'utf8'), /the owner's change\n$/);
  assert.equal(warnings.length, 1, warnings.join('\n'));
});

test('a workspace or record that cannot be read leaves it unguarded, logged once', () => {
  const warnings = [];
  const examples = createExampleGuard({warn: message => warnings.push(message)});
  const gone = path.join(outsideFolder(), 'missing-workspace');
  for (const runId of ['a', 'b']) assert.equal(examples.begin(runId, gone, false), undefined);
  assert.equal(examples.guarding('a'), false);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /could not be read \(ENOENT\)/);
});

test('an unreadable record folder leaves it unguarded, logged once (POSIX)', {skip: POSIX_ONLY}, () => {
  const {root} = workspace();
  const warnings = [];
  const examples = createExampleGuard({warn: message => warnings.push(message)});
  fs.chmodSync(exampleRecordDirectory(), 0);
  try { for (const runId of ['a', 'b']) assert.equal(examples.begin(runId, root, false), undefined); }
  finally { fs.chmodSync(exampleRecordDirectory(), 0o700); }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /could not be read \(EACCES\)/);
});

test('a folder ODS cannot copy back exactly is left to the owner, and the gateway log says so once', () => {
  const {root, example} = workspace();
  fs.symlinkSync(outsideFolder(), path.join(example, 'assets'), DIRECTORY_LINK);
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  for (const prompt of ['Make the snake faster', 'Make the snake slower']) {
    const run = turn(guard, prompt, root);
    assert.equal(run.guidance, undefined);
    fs.appendFileSync(path.join(example, 'ui.js'), '\n// owner\'s change\n');
    assert.equal(run.finish('Done.'), 'Done.');
  }
  assert.equal(warnings.length, 1, warnings.join('\n'));
  assert.match(warnings[0], /is not guarding Playground\/elf-of-doom.*a link/);
  assert.match(fs.readFileSync(path.join(example, 'ui.js'), 'utf8'), /owner's change\n\n\/\/ owner's change\n$/);
});

// Bytes the guard reads while fn runs (every read goes through fs.readSync).
const commonFs = createRequire(import.meta.url)('node:fs');
function bytesRead(fn) {
  const original = commonFs.readSync;
  let total = 0;
  commonFs.readSync = (...args) => { const count = original(...args); total += count; return count; };
  syncBuiltinESMExports();
  try { fn(); } finally { commonFs.readSync = original; syncBuiltinESMExports(); }
  return total;
}

test('the copy is bounded: more entries or bytes than the bounds are not copied, and sizes are checked before any read', () => {
  const folder = scratch('ods-example-guard-bound-');
  fs.mkdirSync(path.join(folder, 'Playground', 'elf-of-doom'), {recursive: true});
  const example = path.join(folder, 'Playground', 'elf-of-doom');
  for (let i = 0; i <= exampleGuardTesting.MAX_ENTRIES; i++) fs.writeFileSync(path.join(example, `f${i}.txt`), 'x');
  assert.match(exampleGuardTesting.takeSnapshot(folder).skipped, /more than 512/);
  fs.rmSync(example, {recursive: true});
  assert.equal(exampleGuardTesting.takeSnapshot(folder), null, 'an absent folder has no copy');
  fs.mkdirSync(path.join(example, 'music'), {recursive: true});
  for (const name of ['a.ogg', 'b.ogg']) fs.writeFileSync(path.join(example, 'music', name), Buffer.alloc(9 * 1024 * 1024, 1));
  let taken;
  assert.equal(bytesRead(() => { taken = exampleGuardTesting.takeSnapshot(folder); }), 0);
  assert.match(taken.skipped, /more than 16 MiB/);
});

test('a folder over 16 MiB is not guarded, and the gateway log says so once', () => {
  const {root, example} = workspace();
  fs.mkdirSync(path.join(example, 'music'));
  for (const name of ['a.ogg', 'b.ogg']) fs.writeFileSync(path.join(example, 'music', name), Buffer.alloc(9 * 1024 * 1024, 1));
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  for (const prompt of ['Make the snake faster', 'Make the snake slower']) {
    const run = turn(guard, prompt, root);
    fs.appendFileSync(path.join(example, 'ui.js'), '\n// mine\n');
    assert.equal(run.finish('Done.'), 'Done.');
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /more than 16 MiB/);
});

test('a file replaced by a larger one is put back without reading the new bytes', () => {
  const {root, example, original} = workspace();
  const examples = createExampleGuard();
  examples.begin('big', root, false);
  fs.writeFileSync(path.join(example, 'ui.js'), Buffer.alloc(24 * 1024 * 1024, 1));
  let outcome;
  assert.ok(bytesRead(() => { outcome = examples.check('big'); }) < 64 * 1024);
  assert.equal(outcome, 'restored');
  assert.deepEqual(tree(example), original);
  examples.end('big');
});

test('a file read is exact: never the bytes of another file than the one checked', () => {
  const folder = outsideFolder();
  fs.writeFileSync(path.join(folder, 'a.txt'), 'first');
  fs.writeFileSync(path.join(folder, 'b.txt'), 'other');
  const {readFile} = exampleGuardTesting;
  assert.equal(readFile(path.join(folder, 'a.txt'), fs.lstatSync(path.join(folder, 'a.txt'))).toString(), 'first');
  assert.equal(readFile(path.join(folder, 'a.txt'), fs.lstatSync(path.join(folder, 'b.txt'))), null);
  assert.equal(readFile(path.join(folder, 'gone.txt'), fs.lstatSync(path.join(folder, 'b.txt'))), null);
});

test('a read never returns more or fewer bytes than the size it was asked for', () => {
  const folder = outsideFolder();
  const file = path.join(folder, 'grows.txt');
  fs.writeFileSync(file, 'four');
  const stat = fs.lstatSync(file);
  fs.appendFileSync(file, ' and more');
  assert.equal(exampleGuardTesting.readFile(file, stat), null);
  fs.writeFileSync(file, 'fo');
  assert.equal(exampleGuardTesting.readFile(file, stat), null);
});

test('a hard-linked file in the game when ODS first copies it leaves the game to the owner: the link stays, no notice, logged once', () => {
  const {root, example} = workspace();
  fs.linkSync(path.join(example, 'engine.js'), path.join(outsideFolder(), 'engine.js'));
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  for (const prompt of ['Make the snake faster', 'Make the snake slower']) assert.equal(turn(guard, prompt, root).finish('Done.'), 'Done.');
  assert.equal(fs.statSync(path.join(example, 'engine.js')).nlink, 2);
  assert.equal(warnings.length, 1, warnings.join('\n'));
  assert.match(warnings[0], /is not guarding Playground\/elf-of-doom.*hard-linked/);
});

test('a file that changes while ODS copies the game leaves the game to the owner', () => {
  const {root, example} = workspace();
  const engine = path.join(example, 'engine.js');
  const original = commonFs.openSync;
  // Something writes engine.js between the size check and the read.
  commonFs.openSync = (file, ...rest) => {
    if (path.resolve(String(file)) === engine) { commonFs.openSync = original; syncBuiltinESMExports(); fs.appendFileSync(engine, '//'); }
    return original(file, ...rest);
  };
  syncBuiltinESMExports();
  let taken;
  try { taken = exampleGuardTesting.takeSnapshot(root); } finally { commonFs.openSync = original; syncBuiltinESMExports(); }
  assert.deepEqual(taken, {skipped: 'it changed while ODS copied it'});
});

test('on a disk that ignores case, a folder found only under another spelling is not copied as the example', {skip: !IGNORES_CASE && 'case-sensitive disk'}, () => {
  const {root, example} = workspace();
  fs.renameSync(example, path.join(root, 'Playground', 'ELF-OF-DOOM'));
  assert.equal(exampleGuardTesting.takeSnapshot(root), null);
});

// ------------------------------------------------------------ other runs --

test('a cron run never names it, whatever its text says', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'In elf-of-doom, make the arrows give 7', root,
    {trigger: 'cron', sessionKey: 'agent:pixel:cron:nightly'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// nightly\n');
  run.finish();
  assert.deepEqual(tree(example), original);
});

// Two chats at once: from the start of a run that names the example until it
// ends, no other run's change to it is undone.
test('from its start to its end, a run that names it holds other runs\' restores back; its end keeps the tree, and then restores resume', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  named.exec("echo '// arrows give 7' >> Playground/elf-of-doom/engine.js");
  // Between the named run's calls, another chat's run changes the game too.
  const other = turn(guard, 'Reformat all my games', root, {sessionKey: chat('e')});
  assert.doesNotMatch(other.exec("echo '// formatted' >> Playground/elf-of-doom/ui.js").text, /\[ODS Portal\]/);
  assert.equal(other.finish('Reformatted.'), 'Reformatted.');
  const both = tree(example);
  assert.equal(named.finish('The arrows give 7 now.'), 'The arrows give 7 now.');
  assert.deepEqual(tree(example), both, 'what the other chat changed meanwhile stays');
  // Once the named run has ended, another run's change is put back to that tree.
  const later = turn(guard, 'Delete all my old games', root, {sessionKey: chat('f')});
  assert.ok(later.exec('rm -r Playground/*').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), both);
  assert.ok(later.finish('Deleted.').endsWith(RESTORED.owner));
});

test('a run still working when a run that names it ends keeps that run\'s change, and puts back only its own', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const other = turn(guard, 'Make the snake faster', root, {sessionKey: chat('e')});
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  const edit = named.start('exec', {command: 'edit', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
  edit.end();
  assert.equal(named.finish('The arrows give 7 now.'), 'The arrows give 7 now.');
  const arrows = tree(example);
  assert.doesNotMatch(other.read('Playground/snake-game/game.js').text, /\[ODS Portal\]/);
  assert.deepEqual(tree(example), arrows, 'the named change is the known-good tree now');
  const stray = other.start('exec', {command: 'format', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// formatted\n');
  assert.ok(stray.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), arrows);
  assert.ok(other.finish('The snake is faster.').endsWith(RESTORED.owner));
});

test('a run that names it holds restores back while its backgrounded command still writes', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const named = turn(guard, 'In elf-of-doom, reformat every file', root);
  // OpenClaw backgrounds the exec after yieldMs: after_tool_call fires while it runs.
  const exec = named.start('exec', {command: 'reformat-all', workdir: '/workspace', yieldMs: 1000});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// formatted\n');
  exec.end('Command still running (session s1).', {status: 'running', sessionId: 's1'});
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// formatted\n');
  assert.equal(turn(guard, 'What can you do?', root, {sessionKey: chat('8')}).finish('I can build games.'), 'I can build games.');
  fs.appendFileSync(path.join(example, 'world.js'), '\n// formatted\n');
  assert.doesNotMatch(named.finish('I reformatted every file.'), /ODS put/);
  for (const file of ['engine.js', 'ui.js', 'world.js']) {
    assert.match(fs.readFileSync(path.join(example, file), 'utf8'), /\/\/ formatted\n$/, file);
  }
});

test('a run that names it and whose end ODS never saw stops counting after its deadline and grace; that ends it', () => {
  let clock = 0;
  const minutes = value => value * 60 * 1000;
  const examples = createExampleGuard({now: () => clock});
  const {root, example} = workspace();
  examples.begin('lost-named-run', root, true, minutes(30));
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
  examples.begin('other-run', root, false);
  assert.equal(examples.guarding('other-run'), true);
  // A later attempt of the named run counts from that attempt.
  clock = minutes(20);
  examples.begin('lost-named-run', root, true, minutes(30));
  clock = minutes(50) + exampleGuardTesting.RUN_GRACE_MS;
  // Two changes and checks: had the named run stopped counting, the first
  // check would end it and the second would put its change back.
  for (const step of ['first', 'second']) {
    fs.appendFileSync(path.join(example, 'ui.js'), `\n// ${step}, held back\n`);
    assert.equal(examples.check('other-run'), undefined, 'held back until the attempt\'s deadline plus the grace');
  }
  assert.ok(exampleGuardTesting.RUN_GRACE_MS >= 120 * 1000, 'the ingress gives a response 120 s past the deadline');
  const named = tree(example);
  clock += 1;
  assert.equal(examples.check('other-run'), undefined, 'its end keeps the tree as it is');
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// not asked\n');
  assert.equal(examples.check('other-run'), 'restored');
  assert.deepEqual(tree(example), named);
  assert.equal(examples.end('other-run'), undefined);
});

// ------------------------------------------------------ delegated work --

const USER = `ods-${'a'.repeat(64)}`;
const OWNER_RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const CHILD = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
const CHILD_RUN = '33333333-2222-4333-8444-555555555555';
// The plugin entry's wiring: delegation custody maps a child and the announced
// continuation to the owner run (index.js exampleOwnerRun).
function delegation() {
  let guard;
  const delivery = createSubagentDelivery({resolveOwnerSession: key => key === OWNER ? {sessionId: `session-${OWNER_RUN}`} : null,
    finalText: message => message.content.filter(block => block.type === 'text').map(block => block.text).join('\n'),
    verificationForRun: runId => guard.deliveryVerificationForRun(runId)});
  guard = createToolLoopGuard({exampleOwnerRun: context => delivery.ownerRunOf(context)});
  return {guard, delivery};
}
function delegate({guard, delivery}, ownerPrompt, childPrompt, root) {
  const owner = {agentId: 'pixel', runId: OWNER_RUN, sessionId: `session-${OWNER_RUN}`, sessionKey: OWNER, trigger: 'user'};
  guard.observeRun(owner, 'pixel', {prompt: ownerPrompt}, {executionHost: 'sandbox', workspaceRoot: root});
  delivery.observe({}, owner);
  guard.observeExampleRun(owner);
  const spawn = {...owner, toolName: 'sessions_spawn', toolCallId: 'spawn'};
  delivery.before({params: {runtime: 'subagent', mode: 'run'}}, spawn);
  delivery.nativeSpawn({runId: CHILD_RUN, childSessionKey: CHILD}, {runId: CHILD_RUN, childSessionKey: CHILD, requesterSessionKey: OWNER});
  delivery.after({result: {details: {status: 'accepted', runId: CHILD_RUN, childSessionKey: CHILD}}}, spawn);
  const yielded = {...owner, toolName: 'sessions_yield', toolCallId: 'yield'};
  delivery.before({params: {}}, yielded);
  delivery.after({result: {details: {status: 'yielded'}}}, yielded);
  delivery.end({success: true}, owner);
  guard.observeAgentEnd({runId: OWNER_RUN, success: true}, owner);
  // The child's own run: its prompt is the task text the model wrote.
  const child = turn(guard, childPrompt, root, {runId: CHILD_RUN, sessionKey: CHILD, trigger: undefined});
  return {owner, child};
}
// The announced continuation's answer as Portal receives it: the delegated
// delivery read, applied by the ingress like any other reply.
function announce({guard, delivery}, owner, root, text) {
  const continuation = {...owner, runId: `announce:v1:${CHILD}:${CHILD_RUN}`,
    inputProvenance: {kind: 'inter_session', sourceTool: 'subagent_announce', sourceSessionKey: CHILD}};
  delivery.observe({prompt: 'The child finished.'}, continuation);
  guard.observeRun(continuation, 'pixel', {prompt: 'The child finished.'}, {executionHost: 'sandbox', workspaceRoot: root});
  guard.observeExampleRun(continuation);
  guard.beforeAgentFinalize({runId: continuation.runId, lastAssistantMessage: text}, continuation, 'pixel');
  delivery.finalize({lastAssistantMessage: text}, continuation);
  delivery.end({success: true, messages: [{role: 'assistant', stopReason: 'stop', content: [{type: 'text', text}]}]}, continuation);
  guard.observeAgentEnd({runId: continuation.runId, success: true}, continuation);
  const read = JSON.parse(JSON.stringify(delivery.read(USER, OWNER_RUN)));
  assert.equal(read.status, 'ready');
  return deliver(read.text, read.verification, OWNER_RUN);
}

test('a delegated child uses its owner run\'s message, never its own task text', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const wired = delegation();
  const {owner, child} = delegate(wired, 'Make the snake faster', 'In elf-of-doom, make the arrows give 7', root);
  assert.equal(child.guidance, undefined, 'the owner\'s message does not mention it');
  assert.ok(child.exec(ENGINE_EDIT).text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  child.finish('Done in elf-of-doom.');
  assert.equal(announce(wired, owner, root, 'The snake is faster.'), `The snake is faster.\n\n${RESTORED.owner}`);
});

test('a delegated child of a turn that names it may change it', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const wired = delegation();
  const {owner, child} = delegate(wired, 'In elf-of-doom, make the arrows give 7', 'Edit engine.js so arrows give 7', root);
  assert.doesNotMatch(child.exec(ENGINE_EDIT).text, /\[ODS Portal\]/);
  child.finish();
  assert.notDeepEqual(tree(example), original);
  assert.equal(announce(wired, owner, root, 'The arrows give 7 now.'), 'The arrows give 7 now.');
});

test('two chats at once: while a named turn\'s child works, another chat\'s change stays; after it, changes are put back', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const wired = delegation();
  const {child} = delegate(wired, 'In elf-of-doom, add three new realms', 'Add three realms to world.js', root);
  child.exec("echo '// realms' >> Playground/elf-of-doom/world.js");
  const other = turn(wired.guard, 'Delete all my old games', root, {sessionKey: chat('9')});
  assert.doesNotMatch(other.exec('rm -r Playground/*/legacy.js').text, /\[ODS Portal\]/);
  assert.equal(other.finish('Deleted.'), 'Deleted.');
  child.finish();
  const kept = tree(example);
  assert.equal(kept['legacy.js'], undefined, 'the documented limit: that change stays');
  assert.match(kept['world.js'], /^file /);
  const next = turn(wired.guard, 'Never mind. Delete all my games.', root, {sessionKey: chat('9')});
  assert.ok(next.exec('rm -r Playground/*').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), kept);
  assert.ok(next.finish('Deleted all your games.').endsWith(RESTORED.owner));
});

test('a later run in a finished child\'s session works for no owner turn', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const wired = delegation();
  const {child} = delegate(wired, 'In elf-of-doom, make the arrows give 7', 'Edit engine.js so arrows give 7', root);
  child.exec(ENGINE_EDIT);
  child.finish();
  const named = tree(example);
  const later = turn(wired.guard, 'Make it faster', root, {runId: '55555555-2222-4333-8444-555555555555', sessionKey: CHILD, trigger: undefined});
  assert.ok(later.exec("sed -i 's/let /var /' Playground/elf-of-doom/engine.js").text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), named);
  later.finish();
});

test('a child with no known owner run never names it', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'In elf-of-doom, make the arrows give 7', root,
    {runId: '44444444-2222-4333-8444-555555555555', sessionKey: 'agent:pixel:subagent:44444444-2222-4333-8444-555555555555'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// child\n');
  run.finish();
  assert.deepEqual(tree(example), original);
});

test('a run whose owner run ODS no longer tracks never names it by its own text', () => {
  for (const sessionKey of [CHILD, OWNER]) {
    const {root, example, original} = workspace();
    const guard = createToolLoopGuard({exampleOwnerRun: () => 'chatcmpl_99999999-2222-4333-8444-555555555555'});
    const run = turn(guard, 'In elf-of-doom, make the arrows give 7', root, {sessionKey});
    fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
    run.finish();
    assert.deepEqual(tree(example), original, sessionKey);
  }
});

test('the per-call notes stay bounded: a call whose result was never persisted drops its note', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make the snake faster', root);
  const unpersisted = [];
  for (let i = 0; i < 300; i++) {
    const toolCallId = `${run.context.runId}-loose-${i}`, ctx = {...run.context, toolName: 'read', toolCallId};
    fs.appendFileSync(path.join(example, 'ui.js'), 'x');
    guard.afterToolCall({toolName: 'read', runId: run.context.runId, params: {path: 'Playground/snake-game/game.js'}, toolCallId,
      result: {content: [{type: 'text', text: 'ok'}]}}, ctx, 'pixel');
    unpersisted.push(ctx);
  }
  const persist = ctx => guard.toolResultPersist({toolName: 'read', toolCallId: ctx.toolCallId,
    message: {role: 'toolResult', toolName: 'read', toolCallId: ctx.toolCallId, content: [{type: 'text', text: 'ok'}]}}, ctx, 'pixel');
  assert.equal(persist(unpersisted[0]), undefined, 'the oldest note was dropped');
  assert.ok(persist(unpersisted.at(-1)).message.content.at(-1).text.endsWith(RESTORED.model));
  run.finish();
});

// ------------------------------------------------- over the real ingress --

const PORTAL_RUN = 'chatcmpl_66666666-2222-4333-8444-555555555555';
// Portal's chat request through the real ingress over loopback HTTP. The
// gateway is the plugin's guard: its chat completion runs the turn (the run
// ends inside that request) and its verification route serves
// deliveryVerificationForRun, as index.js does.
async function portal(t, guard, finish, stream) {
  let reply;
  const server = createIngressServer({token: 'test-gateway-token-0123456789abcdef', gatewayPort: 18789, deps: {setTimeout, clearTimeout,
    fetch: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null;
      let result;
      switch (new URL(url).pathname) {
        case '/v1/chat/completions':
          reply = finish();
          result = {id: PORTAL_RUN, object: 'chat.completion', choices: [{index: 0, message: {role: 'assistant', content: reply}, finish_reason: 'stop'}]};
          break;
        case '/pixel-ods/verification': result = guard.deliveryVerificationForRun(body.runId); break;
        case '/pixel-ods/subagent-delivery': result = {schemaVersion: 1, kind: 'ods-subagent-delivery', runId: body.runId, status: 'not-delegated'}; break;
        case '/pixel-ods/read-only-extension-continuation': result = {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false}; break;
        case '/pixel-ods/unfinished-extension-decision': result = {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false}; break;
        case '/pixel-ods/activity': result = {task: null}; break;
        case '/health': result = {ok: true}; break;
        default: return new Response('', {status: 404});
      }
      return new Response(JSON.stringify(result), {status: 200, headers: {'content-type': 'application/json'}});
    }}});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const response = await fetch(`http://127.0.0.1:${server.address().port}/v1/chat/completions`, {method: 'POST',
    headers: {'content-type': 'application/json', connection: 'close'}, signal: AbortSignal.timeout(10000),
    body: JSON.stringify({user: 'ods-example-guard-portal', stream, messages: [{role: 'user', content: 'hello'}]})});
  const body = await response.text();
  assert.equal(response.status, 200, body);
  return stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)))
    .map(frame => frame.choices?.[0]?.delta?.content ?? '').join('') : JSON.parse(body).choices[0].message.content;
}
const portalTurn = (guard, prompt, root) => turn(guard, prompt, root, {runId: PORTAL_RUN});

for (const stream of [false, true]) {
  test(`Portal receives the restore notice after the model's reply over the real ingress, stream=${stream}`, {skip: NO_BASH}, async t => {
    const {root, example, original} = workspace();
    const guard = createToolLoopGuard();
    const run = portalTurn(guard, 'Change the font to Comic Sans in all my games', root);
    run.exec(`${SED_FONT} Playground/*/style.css`);
    assert.deepEqual(tree(example), original);
    const text = await portal(t, guard, () => { run.finish(); return 'I changed the font in all your games.'; }, stream);
    assert.equal(text, `I changed the font in all your games.\n\n${RESTORED.owner}`);
  });

  test(`Portal receives a preview reply, its evidence and then the notice over the real ingress, stream=${stream}`, async t => {
    const {guard, run} = previewTurn(PORTAL_RUN);
    const text = await portal(t, guard, () => { run.finish(); return 'Your new space game is ready.'; }, stream);
    assert.ok(text.startsWith('Your new space game is ready.\n\n'), text);
    assert.match(text, /Publication scope: [^\n]+\n\nODS put Playground\/elf-of-doom back as it was\./);
    assert.ok(text.endsWith(RESTORED.owner));
    assert.equal(count(text, RESTORED.owner), 1);
  });

  test(`Portal receives the failure notice with the help link over the real ingress, stream=${stream}`, {skip: NO_BASH}, async t => {
    const {root} = workspace();
    const guard = createToolLoopGuard();
    const run = portalTurn(guard, 'Start over with an empty workspace', root);
    run.exec('rm -r Playground && echo "not a folder" > Playground');
    const text = await portal(t, guard, () => { run.finish(); return 'Your workspace is empty now.'; }, stream);
    assert.equal(text, `Your workspace is empty now.\n\n${EXAMPLE_NOTES.failed.owner}`);
    assert.match(text, /\[get help on Discord\]\(https:\/\/discord\.gg\/4ntNp9MAwC\)\.$/);
  });

  test(`a turn ODS did not need to act on reaches Portal unchanged over the real ingress, stream=${stream}`, async t => {
    const {root} = workspace();
    const guard = createToolLoopGuard();
    const run = portalTurn(guard, 'What can you do?', root);
    run.read('Playground/snake-game/game.js');
    assert.equal(await portal(t, guard, () => { run.finish(); return 'I can build games.'; }, stream), 'I can build games.');
  });
}
