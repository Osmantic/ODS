// The shipped example's guard (plugin/example-guard.mjs) driven through the
// real tool-loop guard hooks the plugin entry calls: before_prompt_build
// (observeRun, observeExampleRun), before_tool_call, after_tool_call,
// tool_result_persist, before_agent_finalize, reply_payload_sending and
// agent_end. Each workspace holds the real shipped game files and the
// installers' record in a temporary OPENCLAW_STATE_DIR, and every model
// command really runs in bash (Git Bash on Windows) in that workspace.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import {fileURLToPath} from 'node:url';

import {exampleRecordDirectory, exampleRecordFile} from '../plugin/example-projects.mjs';
import {createExampleGuard, EXAMPLE_NOTES, exampleGuardTesting, namesExample} from '../plugin/example-guard.mjs';
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
// File links need a privilege Windows grants only in developer mode.
const FILE_LINKS = (() => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-guard-link-'));
  try { fs.symlinkSync('target', path.join(folder, 'link'), 'file'); return true; }
  catch (error) { if (error.code === 'EPERM') return false; throw error; }
  finally { fs.rmSync(folder, {recursive: true, force: true}); }
})();
const DIRECTORY_LINK = process.platform === 'win32' ? 'junction' : 'dir';

// ------------------------------------------------------------- the rule --

const NAMES = [
  'In elf-of-doom, make the arrows give 7',
  'in Elf of Doom, make the arrows give 7.',
  'elf-of-doom: add a pause menu',
  'Elf_of_Doom: slow the brutes down',
  // The explicit form names it whatever follows.
  'In elf-of-doom, keep the score but make the arrows faster',
  'In elf-of-doom, build a new game mode with a boss',
  'Make elf-of-doom faster',
  'Elf of Doom is too easy. Make it harder.',
  'Speed up elf-of-doom',
  'Can you speed up elf of doom?',
  'Port elf-of-doom to TypeScript',
  'Let elf-of-doom save high scores',
  'Open elf-of-doom and make the arrows give 7',
  'Open elf-of-doom. Make the arrows give 7.',
  'Fix the bugs in the game in Playground/elf-of-doom.',
  'Can you add a pause button to elf of doom?',
  'Tone down the boss in elf of doom',
  'Clean up elf-of-doom',
  'Translate elf-of-doom into Portuguese',
  'Make Elf of Doom work on my phone',
  'Add touch controls to /workspace/Playground/elf-of-doom',
  'Turn on the sound in elf-of-doom',
  'Elf of Doom crashes on level 3. Can you fix it?',
  'Thanks! Now give elf-of-doom a boss on every level',
  '/goal In elf-of-doom, add three more realms',
];
const DOES_NOT_NAME = [
  'Change the font to Comic Sans in all my games',
  'Make it faster',
  'Make me a new game',
  'Keep elf-of-doom as it is. Change the font to Comic Sans in all my other games.',
  'Keep Elf of Doom, delete the rest of my games',
  'Elf of Doom is fine, leave it. Make the snake faster.',
  'Leave elf-of-doom alone and fix my snake game',
  "Don't touch elf-of-doom. Make the snake faster.",
  'Do not change Elf of Doom; update every other project',
  'Never delete elf-of-doom',
  'Delete all my games except elf-of-doom',
  'Rename every project other than elf of doom',
  'Format all my projects apart from elf-of-doom',
  'Besides elf-of-doom, convert my canvas games to WebGL',
  'Keep elf of doom untouched while you redo the snake game',
  'Preserve elf-of-doom and protect it, then tidy the others',
  'Elf of Doom stays as-is. Delete the rest of my projects.',
  'Take the music from elf-of-doom and add it to the snake game',
  'Copy the sprites out of Playground/elf-of-doom into my platformer',
  "Use elf-of-doom's pause menu in the snake game",
  'Make a new game like elf of doom',
  'Build something similar to elf-of-doom',
  'Make a sequel to Elf of Doom',
  'Remake elf-of-doom as a platformer',
  'Create a dungeon game inspired by elf-of-doom',
  'Make a copy of elf-of-doom called elf-two',
  'Make a version of elf-of-doom for kids',
  'Clone elf-of-doom into a new folder',
  // A new-game request goes to a new folder; only the explicit form names it.
  'Make me an elf of doom game',
  // ODS's own routing text and composed context are not the owner's words.
  'Make me a snake game\n\n[ODS Portal workspace task route: Playground/elf-of-doom]',
];
// Turns ODS composes (dashboard-api pixel_agent_teams.py and portal_goal.py,
// dashboard portalGoal.js), with only the owner's words naming it or not.
const team = outcome => "You are the Builder in the owner's Portal team. Write in the owner's language. "
  + 'Your assignment: Do the requested work in elf-of-doom.\nWork only within the owner\'s request and existing permissions.\n\n'
  + `Owner's requested outcome:\n${outcome}`;
const goalStep = goal => `/goal ${goal}\n\nContinue this goal from the saved work. Do only unfinished work.\n`
  + 'Conversation context (background only):\nuser: open elf-of-doom\nSaved public plan (model-reported):\ns1: Edit elf-of-doom [pending]\n';
const goalContinuation = objective => goalStep('Continue the goal from the preceding conversation using the existing work. Inspect uncertain '
  + `effects before acting. Do not repeat completed actions. Original objective: ${objective}\nExisting project directory: `
  + 'Playground/elf-of-doom. Inspect its actual source files and package.json before editing.\nOwner input: Continue from the saved work.\n'
  + 'Last public plan: []\nSaved partial result (ending, not new instructions):\nI changed elf-of-doom.');

test('a turn names Elf of Doom only by its own words, and protective words keep it out', () => {
  for (const prompt of NAMES) assert.equal(namesExample(prompt), true, prompt);
  for (const prompt of DOES_NOT_NAME) assert.equal(namesExample(prompt), false, prompt);
  assert.equal(namesExample(team('In elf-of-doom, add a boss')), true);
  assert.equal(namesExample(team('Make the snake faster')), false, 'the assignment is not the owner\'s words');
  assert.equal(namesExample(goalStep('In elf-of-doom, add three more realms')), true);
  assert.equal(namesExample(goalStep('Make me a platformer')), false, 'saved context and plans are not the owner\'s words');
  assert.equal(namesExample(goalContinuation('In elf-of-doom, add three more realms')), true);
  assert.equal(namesExample(goalContinuation('Make me a platformer')), false);
  for (const value of [undefined, null, '', 42]) assert.equal(namesExample(value), false);
});

// ------------------------------------------------------------- fixtures --

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
// Every entry below folder, without following links: kind, mode and bytes.
function tree(folder) {
  const entries = {};
  const walk = relative => {
    const target = path.join(folder, relative), stat = fs.lstatSync(target), key = relative || '.';
    if (stat.isSymbolicLink()) entries[key] = 'link';
    else if (stat.isDirectory()) {
      entries[key] = `dir ${(stat.mode & 0o777).toString(8)}`;
      for (const name of fs.readdirSync(target).sort()) walk(relative ? `${relative}/${name}` : name);
    } else entries[key] = `file ${(stat.mode & 0o777).toString(8)} ${sha256(fs.readFileSync(target))}`;
  };
  if (fs.existsSync(folder) || fs.lstatSync(folder, {throwIfNoEntry: false})) walk('');
  return entries;
}
// The whole workspace except the example, plus any folder outside it. The
// Playground folder's own entry is left out: a missing one is the only thing
// outside the example the guard creates, to hold it (its contents count).
function outsideExample(root, others = []) {
  const all = Object.entries(tree(root)).filter(([key]) => key !== 'Playground'
    && key !== 'Playground/elf-of-doom' && !key.startsWith('Playground/elf-of-doom/'));
  return {workspace: Object.fromEntries(all), others: others.map(tree)};
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
function outsideFolder() {
  return scratch('ods-example-guard-outside-');
}
const posix = value => value.replaceAll('\\', '/');

// One model command, run for real in the workspace.
function shell(root, command, env = {}) {
  const result = spawnSync(BASH, ['-c', `export PATH="/usr/bin:$PATH"; ${command}`], {cwd: root, encoding: 'utf8',
    env: {...process.env, WS: posix(root), ...Object.fromEntries(Object.entries(env).map(([key, value]) => [key, posix(value)]))}});
  assert.equal(result.status, 0, `${command}\n${result.stderr}`);
  return result.stdout;
}

const OWNER = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
let runs = 0;
// One owner turn through the plugin's hooks. Every tool call asserts that the
// guard changed nothing outside Playground/elf-of-doom (nor in others).
function turn(guard, prompt, root, {sessionKey = OWNER, runId = `run-${++runs}`, trigger = 'user', others = [], env = {}, ...rest} = {}) {
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey, trigger, ...rest};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost: 'sandbox', workspaceRoot: root});
  guard.observeExampleRun(context);
  let calls = 0;
  const call = (toolName, params, run) => {
    const toolCallId = `${runId}-call-${++calls}`, ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, runId, params, toolCallId}, ctx, 'pixel');
    assert.notEqual(before?.block, true, before?.blockReason);
    const used = before?.params ?? params;
    const output = run(used);
    const afterTool = outsideExample(root, others);
    const result = {content: [{type: 'text', text: output || 'ok'}], details: {status: 'completed', exitCode: 0}};
    guard.afterToolCall({toolName, runId, params: used, toolCallId, result}, ctx, 'pixel');
    const message = {role: 'toolResult', toolName, toolCallId, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId, message}, ctx, 'pixel')?.message ?? message;
    assert.deepEqual(outsideExample(root, others), afterTool, 'the guard touches nothing outside Playground/elf-of-doom');
    return {params: used, text: persisted.content.map(block => block.text).join('\n')};
  };
  return {
    context,
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

// ------------------------------------------------------ non-naming turns --

const BULK = [
  ['Change the font to Comic Sans in all my games', `${SED_FONT} $(grep -rl font-family "$WS/Playground")`],
  ['Change the font to Comic Sans in all my games', `for f in $(find Playground -name '*.css'); do ${SED_FONT} "$f"; done`],
  ['Change the font to Comic Sans in all my games', `find Playground -name '*.css' | while read f; do ${SED_FONT} "$f"; done`],
  ['Change the font to Comic Sans in all my games',
    `node -e "const fs=require('fs');for(const d of fs.readdirSync('Playground')){const f='Playground/'+d+'/style.css';if(fs.existsSync(f))fs.writeFileSync(f,'body{font-family:Comic Sans MS}')}"`],
];
for (const [prompt, command] of BULK) {
  test(`a turn that does not name it gets the game back after the call and the run: ${command}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const run = turn(createToolLoopGuard(), prompt, root);
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
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom']);
  run.finish();
  assert.deepEqual(tree(example), original);
});

test('deleting Playground itself brings back a Playground holding only the example', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Delete all my projects', root);
  run.exec('rm -r Playground');
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom']);
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
  const routed = turn(guard, 'Make me a new game', root, {sessionKey: `agent:pixel:openai-user:ods-${'b'.repeat(64)}`});
  const {params, text} = routed.write('Playground/elf-of-doom/index.html', '<!doctype html><title>New game</title>\n');
  assert.equal(params.path, 'Playground/elf-of-doom-2/index.html', 'project routing picks a new folder');
  assert.doesNotMatch(text, /\[ODS Portal\] ODS put/);
  assert.deepEqual(tree(example), original);
  // After reading the example, routing keeps the path; the guard puts it back.
  const read = turn(guard, 'Make me a new game', root, {sessionKey: `agent:pixel:openai-user:ods-${'c'.repeat(64)}`});
  read.read('Playground/elf-of-doom/index.html');
  const landed = read.write('Playground/elf-of-doom/index.html', '<!doctype html><title>New game</title>\n');
  assert.equal(landed.params.path, 'Playground/elf-of-doom/index.html');
  assert.ok(landed.text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), original);
  assert.match(read.finish('Your new game is ready.'), new RegExp(`${RESTORED.owner.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`));
});

// ---------------------------------------------------- protective namings --

const PROTECTIVE = [
  ['Keep elf-of-doom as it is. Change the font to Comic Sans in all my other games.', `${SED_FONT} Playground/*/style.css`, 'snake-game/style.css'],
  ['Keep Elf of Doom, delete the rest of my games', 'rm -r Playground/*', null],
  ['Elf of Doom is fine, leave it. Make the snake faster.', "sed -i 's/speed = 1/speed = 2/' Playground/*/game.js && sed -i 's/const /let /' Playground/*/*.js", 'snake-game/game.js'],
  ['Take the music from elf-of-doom and add it to the snake game', 'mv Playground/elf-of-doom/engine.js Playground/snake-game/music.js', 'snake-game/music.js'],
  ['Make a new game like elf of doom', 'mkdir -p Playground/elf-like && cp -r Playground/elf-of-doom/* Playground/elf-like/ && echo "<p>new</p>" >> Playground/elf-of-doom/index.html', 'elf-like/index.html'],
];
for (const [prompt, command, kept] of PROTECTIVE) {
  test(`a turn that names it only to keep, exclude or copy it gets it back: ${prompt}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const guard = createToolLoopGuard();
    const run = turn(guard, prompt, root);
    if (prompt.startsWith('Make a new game')) {
      // A new project's first step is a file of its own.
      run.write('Playground/elf-like/notes.txt', 'like elf of doom\n');
    }
    assert.ok(run.exec(command).text.endsWith(RESTORED.model));
    assert.deepEqual(tree(example), original);
    if (kept) assert.ok(fs.existsSync(path.join(root, 'Playground', ...kept.split('/'))), `${kept} stays as the command left it`);
    else assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom']);
    assert.ok(run.finish().endsWith(RESTORED.owner));
  });
}

// --------------------------------------------------------- named changes --

const NAMED = ['In elf-of-doom, make the arrows give 7', 'Make elf-of-doom faster', 'Elf of Doom is too easy. Make it harder.',
  'Speed up elf-of-doom', 'Port elf-of-doom to TypeScript', 'Open elf-of-doom and make the arrows give 7'];
for (const prompt of NAMED) {
  test(`a turn that names it changes it, and the change sticks: ${prompt}`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace();
    const run = turn(createToolLoopGuard(), prompt, root);
    const {text} = run.exec("sed -i 's/const /let /' Playground/elf-of-doom/engine.js && echo '// typed' > Playground/elf-of-doom/types.ts");
    const changed = tree(example);
    assert.notDeepEqual(changed, original);
    assert.doesNotMatch(text, /\[ODS Portal\]/);
    assert.equal(run.finish('Done.'), 'Done.', 'no notice');
    assert.deepEqual(tree(example), changed);
  });
}

test('nothing carries over: after a named change, "Make it faster" is put back to that change', {skip: NO_BASH}, () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const first = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  first.exec("sed -i 's/const /let /' Playground/elf-of-doom/engine.js");
  first.finish();
  const named = tree(example);
  assert.notDeepEqual(named, original);
  const next = turn(guard, 'Make it faster', root);
  assert.ok(next.exec("sed -i 's/let /var /' Playground/elf-of-doom/engine.js").text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), named);
  assert.ok(next.finish().endsWith(RESTORED.owner));
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

test('when the folder cannot be put back without touching Playground, the owner hears it with the help link', {skip: NO_BASH}, () => {
  const {root, example} = workspace();
  const warnings = [];
  const run = turn(createToolLoopGuard({warn: message => warnings.push(message)}), 'Start over with an empty workspace', root);
  const {text} = run.exec('rm -r Playground && echo "not a folder" > Playground');
  assert.equal(fs.readFileSync(path.join(root, 'Playground'), 'utf8'), 'not a folder\n', 'ODS never replaces Playground itself');
  assert.equal(fs.existsSync(example), false);
  assert.ok(text.endsWith(EXAMPLE_NOTES.failed.model));
  const reply = run.finish('Your workspace is empty now.');
  assert.ok(reply.endsWith(EXAMPLE_NOTES.failed.owner));
  assert.match(EXAMPLE_NOTES.failed.owner, /https:\/\/discord\.gg\/4ntNp9MAwC/);
  assert.match(warnings.join('\n'), /could not put Playground\/elf-of-doom back/);
});

test('a change made outside any tool call is put back at the end of the run, before the reply', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'Make the snake faster', root);
  // A background process the run started writes later.
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

// ------------------------------------------------------------- whose copy --

for (const record of ['present', null]) {
  test(`the owner's own Playground/elf-of-doom is never guarded (record: ${record})`, {skip: NO_BASH}, () => {
    const {root, example, original} = workspace({record});
    const run = turn(createToolLoopGuard(), 'Change the font to Comic Sans in all my games', root);
    assert.doesNotMatch(run.exec(`${SED_FONT} Playground/*/style.css`).text, /\[ODS Portal\]/);
    assert.equal(run.finish('Done.'), 'Done.');
    assert.notDeepEqual(tree(example), original);
  });
}

test('a folder ODS cannot copy back exactly is left to the owner, and the gateway log says so once', () => {
  const {root, example} = workspace();
  const outside = outsideFolder();
  fs.symlinkSync(outside, path.join(example, 'assets'), DIRECTORY_LINK);
  const warnings = [];
  const guard = createToolLoopGuard({warn: message => warnings.push(message)});
  for (const prompt of ['Make the snake faster', 'Make the snake slower']) {
    const run = turn(guard, prompt, root);
    fs.appendFileSync(path.join(example, 'ui.js'), '\n// owner\'s change\n');
    assert.equal(run.finish('Done.'), 'Done.');
  }
  assert.equal(warnings.filter(message => /is not guarding Playground\/elf-of-doom/.test(message)).length, 1);
  assert.match(warnings[0], /a link or special file/);
  assert.match(fs.readFileSync(path.join(example, 'ui.js'), 'utf8'), /owner's change\n\n\/\/ owner's change\n$/);
});

test('the copy is bounded: more entries than the bound are not copied', () => {
  const folder = scratch('ods-example-guard-bound-');
  for (let i = 0; i <= exampleGuardTesting.MAX_ENTRIES; i++) fs.writeFileSync(path.join(folder, `f${i}.txt`), 'x');
  assert.match(exampleGuardTesting.takeSnapshot(folder).skipped, /more than 512/);
  assert.equal(exampleGuardTesting.takeSnapshot(path.join(folder, 'absent')), null);
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

test('while a run that names it is active, other runs do not restore; when it ends they copy it again', () => {
  const {root, example, original} = workspace();
  const guard = createToolLoopGuard();
  const other = turn(guard, 'Make the snake faster', root, {sessionKey: `agent:pixel:openai-user:ods-${'e'.repeat(64)}`});
  const named = turn(guard, 'In elf-of-doom, make the arrows give 7', root);
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// arrows give 7\n');
  assert.doesNotMatch(other.read('Playground/snake-game/game.js').text, /\[ODS Portal\]/);
  named.finish();
  const kept = tree(example);
  assert.notDeepEqual(kept, original, 'the named change sticks');
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// not asked\n');
  assert.ok(other.read('Playground/snake-game/game.js').text.endsWith(RESTORED.model));
  assert.deepEqual(tree(example), kept);
  other.finish();
});

test('a run that names it while another is active keeps its change, and a later run is guarded again', () => {
  const {root, example} = workspace();
  const guard = createToolLoopGuard();
  const named = turn(guard, 'Speed up elf-of-doom', root);
  const other = turn(guard, 'Make the snake faster', root, {sessionKey: `agent:pixel:openai-user:ods-${'f'.repeat(64)}`});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// faster\n');
  other.finish();
  named.finish();
  const kept = tree(example);
  const later = turn(guard, 'Make it faster', root);
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// not asked\n');
  later.finish();
  assert.deepEqual(tree(example), kept);
});

test('a run whose end ODS never saw stops counting after the bound', () => {
  let clock = 0;
  const warnings = [];
  const examples = createExampleGuard({now: () => clock, warn: message => warnings.push(message)});
  const {root, example, original} = workspace();
  examples.begin('lost-named-run', root, true);
  clock += exampleGuardTesting.RUN_TTL_MS + 1;
  examples.begin('later-run', root, false);
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// not asked\n');
  assert.equal(examples.check('later-run'), 'restored');
  assert.deepEqual(tree(example), original);
  assert.equal(examples.end('later-run'), undefined);
  assert.deepEqual(warnings, []);
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
  assert.ok(child.exec("sed -i 's/const /let /' Playground/elf-of-doom/engine.js").text.endsWith(RESTORED.model));
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
  assert.doesNotMatch(child.exec("sed -i 's/const /let /' Playground/elf-of-doom/engine.js").text, /\[ODS Portal\]/);
  child.finish();
  assert.notDeepEqual(tree(example), original);
  assert.equal(announce(wired, owner, root, 'The arrows give 7 now.').text, 'The arrows give 7 now.');
});

test('a child with no known owner run never names it', () => {
  const {root, example, original} = workspace();
  const run = turn(createToolLoopGuard(), 'In elf-of-doom, make the arrows give 7', root,
    {runId: '44444444-2222-4333-8444-555555555555', sessionKey: 'agent:pixel:subagent:44444444-2222-4333-8444-555555555555'});
  fs.appendFileSync(path.join(example, 'engine.js'), '\n// child\n');
  run.finish();
  assert.deepEqual(tree(example), original);
});
