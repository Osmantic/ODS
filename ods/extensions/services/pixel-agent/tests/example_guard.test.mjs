// The shipped example's guard (plugin/example-guard.mjs) driven through the
// real tool-loop guard hooks the plugin entry calls: before_prompt_build
// (observeRun, observeExampleRun, exampleContextForRun), before_tool_call,
// after_tool_call, tool_result_persist, before_agent_finalize,
// reply_payload_sending and agent_end. Each workspace holds the real shipped
// game files and the installers' record in a temporary OPENCLAW_STATE_DIR,
// and every model command really runs in bash (Git Bash on Windows) in that
// workspace. example_guard_wiring.test.mjs runs the same hooks through the
// plugin entry's own registration code.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import {createRequire, syncBuiltinESMExports} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import {fileURLToPath} from 'node:url';

import {exampleRecordDirectory, exampleRecordFile} from '../plugin/example-projects.mjs';
import {createExampleGuard, EXAMPLE_NOTES, exampleGuardTesting, mentionsExample, namesExample} from '../plugin/example-guard.mjs';
import {createPendingClarifications} from '../plugin/pending-clarification.mjs';
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
function tree(folder, {links = true} = {}) {
  const entries = {};
  const walk = relative => {
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
  const all = Object.entries(tree(root, {links: false})).filter(([key]) => key !== 'Playground' && !example.test(key));
  return {workspace: Object.fromEntries(all), others: others.map(folder => tree(folder, {links: false}))};
}

// A workspace holding the real shipped game, the installers' record (by
// default saying ODS added it) and two of the owner's own games.
function workspace({record = 'added'} = {}) {
  const root = scratch('ods-example-guard-');
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
    // The model's final reply as the owner receives it, then the run's end.
    finish(text = 'Done.') {
      guard.beforeAgentFinalize({runId, lastAssistantMessage: text}, context, 'pixel');
      const reply = guard.replyPayloadSending({runId, kind: 'final', payload: {text}})?.payload?.text ?? text;
      guard.observeAgentEnd({runId, success: true}, context);
      return reply;
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

// ------------------------------------------- the known-good tree --

test('a change made between runs is put back when the next run that does not name it starts, and the owner hears it', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  turn(guard, 'Make the snake faster', root).finish();
  // A background process the run started, or a hand edit, writes later.
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// formatted\n');
  fs.rmSync(path.join(example, 'legacy.js'));
  const next = turn(guard, 'What can you do?', root);
  assert.deepEqual(tree(example), original, 'put back before the run works');
  assert.equal(next.finish('I can build games.'), `I can build games.\n\n${RESTORED.owner}`);
});

test('a run that names it does not undo a change made before it, and its end makes that tree the known-good one', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  turn(guard, 'Make the snake faster', root).finish();
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// my own change\n');
  const named = turn(guard, 'In elf-of-doom, keep my change', root);
  const kept = tree(example);
  assert.equal(named.finish('Kept.'), 'Kept.');
  assert.equal(turn(guard, 'Make the snake slower', root).finish('Done.'), 'Done.');
  assert.deepEqual(tree(example), kept);
});

test('after a gateway restart the folder as ODS first finds it is the known-good tree', () => {
  const {root, example} = workspace();
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// before the restart\n');
  const restarted = createToolLoopGuard();
  const first = turn(restarted, 'Make the snake faster', root);
  const found = tree(example);
  assert.equal(first.finish('Done.'), 'Done.');
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// after\n');
  assert.ok(turn(restarted, 'Make the snake slower', root).finish().endsWith(RESTORED.owner));
  assert.deepEqual(tree(example), found);
});

test('a change made outside any tool call is put back at the end of the run, before the reply', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root);
  fs.appendFileSync(path.join(example, 'ui.js'), '\n// late\n');
  assert.ok(run.finish('The snake is faster.').endsWith(RESTORED.owner));
  assert.deepEqual(tree(example), original);
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

test('a reply ODS replaces with its own text keeps the notice, for the reply and the ingress alike', {skip: NO_BASH}, () => {
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
  assert.ok(verification.text.endsWith(`\n\n${RESTORED.owner}`));
  assert.equal(reply, verification.text);
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

test('a new game\'s verified preview reply keeps the notice before the preview evidence', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const run = turn(guard, 'Make me a new space game', root, {sessionKey: chat('d')});
  const content = '<!doctype html><title>Space game</title>';
  const {params} = run.write('Playground/space-game/index.html', content);
  const directory = path.posix.dirname(params.path);
  // A stray change to the example while the model checks its new file.
  const stray = run.start('read', {path: params.path});
  fs.appendFileSync(path.join(example, 'ui.js'), 'x\n');
  assert.ok(stray.end(content).text.endsWith(RESTORED.model));
  const preview = run.start('pixel_ods_workspace_preview', {relativeDirectory: directory});
  preview.end('Published.', previewReceipt(preview.params.relativeDirectory, content));
  assert.equal(guard.deliveryVerificationForRun(run.context.runId).deliveryMode, 'append');
  const reply = run.finish('Your new space game is ready.');
  assert.ok(reply.startsWith(`Your new space game is ready.\n\n${RESTORED.owner}\n\n`), reply);
  assert.match(reply, /Publication scope/);
  assert.deepEqual(tree(example), original);
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
  run.exec('cp -p Playground/elf-of-doom/engine.js "$OUT/stamp" && sed -i \'s/const /CONST/\' Playground/elf-of-doom/engine.js'
    + ' && touch -r "$OUT/stamp" Playground/elf-of-doom/engine.js');
  assert.deepEqual(tree(example), original);
  run.finish();
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
  assert.equal(warnings.length, 1, warnings.join('\n'));
  assert.match(warnings[0], /is not guarding Playground\/elf-of-doom.*link or not a folder/);
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

// ------------------------------------------------------------ other runs --

test('a cron run never names it, whatever its text says', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'In elf-of-doom, make the arrows give 7', root,
    {trigger: 'cron', sessionKey: 'agent:pixel:cron:nightly'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// nightly\n');
  run.finish();
  assert.deepEqual(tree(example), original);
});

test('a named run that is between calls does not hold another run\'s restore back', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  const edit = named.start('exec', {command: 'edit', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
  edit.end();
  const arrows = tree(example);
  assert.notDeepEqual(arrows, original);
  const other = turn(guard, 'Delete all my old games', root, {sessionKey: chat('e')});
  const deletion = other.start('exec', {command: 'rm -r Playground/*', workdir: '/workspace'});
  fs.rmSync(example, {recursive: true});
  assert.ok(deletion.end().text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), arrows, 'put back to the named run\'s change');
  other.finish();
  named.finish();
  assert.deepEqual(tree(example), arrows);
});

test('while a named run\'s call is running, other runs leave the folder alone, and that change sticks', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const other = turn(guard, 'Make the snake faster', root, {sessionKey: chat('f')});
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  const edit = named.start('exec', {command: 'edit', workdir: '/workspace'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
  assert.doesNotMatch(other.read('Playground/snake-game/game.js').text, /\[ODS Portal\]/);
  edit.end();
  const kept = tree(example);
  assert.equal(other.finish('Done.'), 'Done.');
  named.finish();
  assert.deepEqual(tree(example), kept);
});

test('a named run whose end ODS never saw stops holding restores back after its deadline', () => {
  let clock = 0;
  const examples = createExampleGuard({now: () => clock});
  const {root, example, original} = workspace();
  examples.begin('lost-named-run', root, true, 30 * 60 * 1000);
  examples.callStarts('lost-named-run', 'call-1');
  examples.begin('other-run', root, false);
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// not asked\n');
  assert.equal(examples.check('other-run'), undefined, 'held back while that call may still run');
  clock += 30 * 60 * 1000 + exampleGuardTesting.RUN_GRACE_MS + 1;
  assert.equal(examples.check('other-run'), 'restored');
  assert.deepEqual(tree(example), original);
  examples.begin('later-run', root, false);
  assert.equal(examples.guarding('lost-named-run'), false, 'forgotten');
  assert.equal(examples.end('later-run'), undefined);
});

// ------------------------------------------------------ delegated work --

const USER = `ods-${'a'.repeat(64)}`;
const OWNER_RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const CHILD = 'agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
const CHILD_RUN = '33333333-2222-4333-8444-555555555555';
// The plugin entry's wiring: delegation custody maps a child and the announced
// continuation to the owner run (index.js exampleOwnerRun, noticeForRun).
function delegation() {
  let guard;
  const delivery = createSubagentDelivery({resolveOwnerSession: key => key === OWNER ? {sessionId: `session-${OWNER_RUN}`} : null,
    finalText: message => message.content.filter(block => block.type === 'text').map(block => block.text).join('\n'),
    verificationForRun: runId => guard.deliveryVerificationForRun(runId), noticeForRun: runId => guard.exampleNoticeForRun(runId)});
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
  return delivery.read(USER, OWNER_RUN);
}

test('a delegated child uses its owner run\'s message, never its own task text', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const wired = delegation();
  const {owner, child} = delegate(wired, 'Make the snake game faster', 'In elf-of-doom, make the arrows give 7', root);
  assert.equal(child.guidance, undefined, 'the owner\'s message does not mention it');
  assert.ok(child.exec(ENGINE_EDIT).text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  child.finish('Done in elf-of-doom.');
  const delivered = announce(wired, owner, root, 'The snake game is faster.');
  assert.equal(delivered.status, 'ready');
  assert.equal(delivered.text, `The snake game is faster.\n\n${RESTORED.owner}`);
});

test('a delegated child of a turn that names it may change it', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const wired = delegation();
  const {owner, child} = delegate(wired, 'In elf-of-doom, make the arrows give 7', 'Edit engine.js so arrows give 7', root);
  assert.doesNotMatch(child.exec(ENGINE_EDIT).text, /\[ODS Portal\]/);
  child.finish();
  assert.notDeepEqual(tree(example), original);
  assert.equal(announce(wired, owner, root, 'The arrows give 7 now.').text, 'The arrows give 7 now.');
});

test('a named turn\'s child that outlives it does not unguard the owner\'s next turn', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const wired = delegation();
  const {child} = delegate(wired, 'In elf-of-doom, add three new realms', 'Add three realms to world.js', root);
  child.exec("echo '// realms' >> Playground/elf-of-doom/world.js");
  const realms = tree(example);
  const next = turn(wired.guard, 'Never mind. Delete all my games.', root);
  assert.ok(next.exec('rm -r Playground/*').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), realms);
  assert.ok(next.finish('Deleted all your games.').endsWith(RESTORED.owner));
  child.finish();
  assert.deepEqual(tree(example), realms);
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
