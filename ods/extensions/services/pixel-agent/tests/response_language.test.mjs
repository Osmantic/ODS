import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  ENGLISH_RESPONSE_CONTRACT, ENGLISH_TURN_REMINDER, GOAL_TURN_LANGUAGE_NOTE, RESPONSE_LANGUAGE_CONTRACT, RESPONSE_LANGUAGE_FILE,
  TEAM_TURN_LANGUAGE_NOTE, inResponseLanguage, readResponseLanguage, responseLanguageContract, responseLanguageTurnContext,
} from '../plugin/response-language.mjs';
import {GOAL_CONTRACT} from '../plugin/goal-progress.mjs';
import {promptContractForAgent, PORTAL_INTEGRATION_TURN_CONTEXT} from '../plugin/prompt-contract.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {ACTIVITY_CONTRACT} from '../plugin/activity-display.mjs';
import {STOP_SYNTHESIS_INSTRUCTION, synthesisRequest} from '../plugin/stop-synthesis.mjs';
import {createCompletionAssurance, executionContext, UNREAD_SOURCES_REPLACEMENT} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard, RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {dashboardApiTurns, promptHookAlone, PYTHON_AVAILABLE, realPromptBuild} from './prompt-build-harness.mjs';

function stateDir(content) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-response-language-'));
  if (content !== undefined) fs.writeFileSync(path.join(dir, RESPONSE_LANGUAGE_FILE), content);
  return dir;
}

test('the English setting is read from the OpenClaw state folder, and anything else keeps the default', () => {
  const warnings = [];
  const warn = message => warnings.push(message);
  assert.equal(readResponseLanguage(stateDir(), warn), undefined);
  assert.equal(readResponseLanguage(stateDir('en\n'), warn), 'en');
  assert.equal(warnings.length, 0, 'a missing file is the ordinary default');
  assert.equal(readResponseLanguage(stateDir('pt\n'), warn), undefined);
  const unreadable = stateDir();
  fs.mkdirSync(path.join(unreadable, RESPONSE_LANGUAGE_FILE));
  assert.equal(readResponseLanguage(unreadable, warn), undefined);
  assert.equal(warnings.length, 2, 'an unusable setting is reported, never fatal');
  // Without OPENCLAW_STATE_DIR the plugin reads ~/.openclaw, where the installer writes the file.
  const home = stateDir();
  fs.mkdirSync(path.join(home, '.openclaw'));
  fs.writeFileSync(path.join(home, '.openclaw', RESPONSE_LANGUAGE_FILE), 'en\n');
  const saved = {state: process.env.OPENCLAW_STATE_DIR, home: process.env.HOME, profile: process.env.USERPROFILE};
  try {
    delete process.env.OPENCLAW_STATE_DIR;
    process.env.HOME = home;
    process.env.USERPROFILE = home;
    assert.equal(readResponseLanguage(), 'en');
  } finally {
    for (const [key, value] of [['OPENCLAW_STATE_DIR', saved.state], ['HOME', saved.home], ['USERPROFILE', saved.profile]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test('English-only replaces the language rule on full, lean, small-context and reviewer prompts', () => {
  for (const options of [
    {configuredContextWindow: 131072},
    {configuredContextWindow: 131072, configuredLeanPrompt: true},
    {configuredContextWindow: 16384},
  ]) {
    for (const executionHost of ['sandbox', 'gateway']) {
      for (const prompt of ['Create a nice web page for me', 'Crie uma página web bonita para mim', 'OK']) {
        const base = {...options, executionHost};
        const english = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt},
          {...base, configuredResponseLanguage: 'en'}).appendSystemContext;
        assert.ok(english.includes(ENGLISH_RESPONSE_CONTRACT));
        assert.ok(!english.includes(RESPONSE_LANGUAGE_CONTRACT));
        const plain = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt}, base).appendSystemContext;
        assert.ok(plain.includes(RESPONSE_LANGUAGE_CONTRACT));
        assert.ok(!plain.includes(ENGLISH_RESPONSE_CONTRACT));
      }
    }
  }
  const reviewer = promptContractForAgent({agentId: 'pixel'}, 'pixel',
    {prompt: "Identity: Portal\n\nYou are the Reviewer in the owner's Portal team.\nOwner request: build a website."},
    {configuredResponseLanguage: 'en'}).appendSystemContext;
  assert.ok(reviewer.includes(ENGLISH_RESPONSE_CONTRACT));
  assert.ok(!reviewer.includes(RESPONSE_LANGUAGE_CONTRACT));
});

test('clarification cards follow the configured language', () => {
  assert.ok(createAskUserTool({responseLanguage: 'en'}).description.includes(ENGLISH_RESPONSE_CONTRACT));
  assert.ok(createAskUserTool().description.includes(RESPONSE_LANGUAGE_CONTRACT));
  assert.equal(responseLanguageContract('fr'), RESPONSE_LANGUAGE_CONTRACT);
});

test('every fixed instruction that says where its text goes says English when English-only', () => {
  for (const instruction of [ACTIVITY_CONTRACT, GOAL_CONTRACT, executionContext(), STOP_SYNTHESIS_INSTRUCTION,
    RECURSIVE_DELETE_REQUIRES_OWNER_REASON]) {
    assert.doesNotMatch(instruction, /owner(?:'s)? language/, 'no instruction names the owner language');
    const english = inResponseLanguage(instruction, 'en');
    assert.notEqual(english, instruction, 'the instruction has a phrase English-only mode rewrites');
    assert.doesNotMatch(english, /reply language|language of the owner request/);
    assert.match(english, /in English/);
    assert.equal(inResponseLanguage(instruction, undefined), instruction);
  }
  const request = synthesisRequest({request: 'Quais são os eventos?', pages: [], language: 'en'});
  assert.equal(request.systemPrompt, inResponseLanguage(STOP_SYNTHESIS_INSTRUCTION, 'en'));
  assert.equal(synthesisRequest({request: 'x', pages: []}).systemPrompt, STOP_SYNTHESIS_INSTRUCTION);
});

test('English-only reads ambiguous answer dates month-first and keeps the English notice', () => {
  const answer = 'The event is on 05/11/2026 (https://example.org/a).';
  const candidates = options => {
    const guard = createCompletionAssurance(options);
    guard.begin('Pesquise na internet e abra as fontes antes de citar.');
    guard.observe('web_search', {result: {details: {results: [{url: 'https://example.org/a'}]}}});
    return guard.hostVerificationCandidates(answer);
  };
  // The citation check reads 05/11 as 5 November for a Portuguese request,
  // but the answer is English in English-only mode: May 11.
  assert.deepEqual(candidates(), {urls: ['https://example.org/a'], portuguese: true});
  assert.deepEqual(candidates({englishOnly: true}), {urls: ['https://example.org/a'], portuguese: false});
  const english = createCompletionAssurance({englishOnly: true});
  english.begin('Pesquise na internet e abra as fontes antes de citar.');
  assert.equal(english.finalize('O evento é em 2026-10-01: https://example.org/a')?.action, 'revise');
  assert.equal(english.terminal, UNREAD_SOURCES_REPLACEMENT);
});

// The actual prompt hook from index.js, with its collaborators stubbed.
const promptHook = (language, goal, clarificationNote = undefined, prompt = undefined) =>
  promptHookAlone({language, goal, clarificationNote, prompt});
const besideOwner = (...parts) => [...parts, PORTAL_INTEGRATION_TURN_CONTEXT].filter(Boolean).join('\n\n');

test('English-only puts the reminder beside the owner message on every turn, after goal mode', async () => {
  const goal = inResponseLanguage(GOAL_CONTRACT, 'en');
  const english = await promptHook('en', false);
  assert.equal(english.appendContext, besideOwner(ENGLISH_TURN_REMINDER));
  assert.ok(english.appendSystemContext.includes(ENGLISH_RESPONSE_CONTRACT));
  assert.ok(english.appendSystemContext.includes(inResponseLanguage(ACTIVITY_CONTRACT, 'en')));
  assert.ok(english.appendSystemContext.includes(inResponseLanguage(executionContext(), 'en')));
  assert.doesNotMatch(english.appendSystemContext, /reply language/);
  assert.equal((await promptHook('en', true)).appendContext, besideOwner(goal, ENGLISH_TURN_REMINDER));
  const plain = await promptHook(undefined, false);
  assert.equal(plain.appendContext, PORTAL_INTEGRATION_TURN_CONTEXT, 'the default adds no language reminder');
  assert.ok(plain.appendSystemContext.includes(ACTIVITY_CONTRACT));
  assert.ok(plain.appendSystemContext.includes(RESPONSE_LANGUAGE_CONTRACT));
  assert.equal((await promptHook(undefined, true)).appendContext, besideOwner(GOAL_CONTRACT, GOAL_TURN_LANGUAGE_NOTE));
  // The reminder replaces the default team and goal notes, never joins them.
  assert.equal(responseLanguageTurnContext(undefined), '');
  assert.equal(responseLanguageTurnContext(undefined, {teamRole: 'Coordinator'}), '');
  assert.equal(responseLanguageTurnContext(undefined, {teamRole: 'Reviewer'}), TEAM_TURN_LANGUAGE_NOTE);
  assert.equal(responseLanguageTurnContext(undefined, {goal: true}), GOAL_TURN_LANGUAGE_NOTE);
  for (const turn of [{}, {teamRole: 'Builder'}, {goal: true}]) assert.equal(responseLanguageTurnContext('en', turn), ENGLISH_TURN_REMINDER);
});

test('a clarification answer note follows the goal contract and the language note or reminder', async () => {
  const note = '[ODS Portal clarification note]';
  const goal = inResponseLanguage(GOAL_CONTRACT, 'en');
  assert.equal((await promptHook('en', true, note)).appendContext, besideOwner(goal, ENGLISH_TURN_REMINDER, note));
  assert.equal((await promptHook('en', false, note)).appendContext, besideOwner(ENGLISH_TURN_REMINDER, note));
  assert.equal((await promptHook(undefined, true, note)).appendContext, besideOwner(GOAL_CONTRACT, GOAL_TURN_LANGUAGE_NOTE, note));
  assert.equal((await promptHook(undefined, false, note)).appendContext, besideOwner(note));
});

test('the plugin entry reads the setting once and hands it to the guard and the clarification tool', () => {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  assert.equal(source.match(/readResponseLanguage\(/g)?.length, 1);
  const begin = source.indexOf('toolLoopGuardRegistry.get({');
  const guardOptions = source.slice(begin, source.indexOf('\n    });', begin));
  assert.ok(begin >= 0 && guardOptions.length > 0);
  assert.match(guardOptions, /\n\s+responseLanguage: configuredResponseLanguage,\n/);
  assert.match(source, /createAskUserTool\(\{responseLanguage: configuredResponseLanguage\}\)/);
});

test('English-only stops a recursive deletion with an English instruction and notice', () => {
  const context = {agentId: 'pixel', runId: 'english-only', sessionId: 'session-english', sessionKey: 'agent:pixel:english'};
  const guard = createToolLoopGuard({abortRun: () => {}, responseLanguage: 'en'});
  guard.observeRun(context, 'pixel', {prompt: 'Analise as limitações do workspace e explique quais recursos estão disponíveis.'});
  const english = inResponseLanguage(RECURSIVE_DELETE_REQUIRES_OWNER_REASON, 'en');
  assert.notEqual(english, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  const blocked = guard.beforeToolCall({toolName: 'exec', params: {command: 'rm -rf /tmp/probe_venv'}}, context);
  assert.equal(blocked.blockReason, english);
  // Any tool call after the refusal is stopped by a separate check, with the same reason.
  const retried = guard.beforeToolCall({toolName: 'exec', params: {command: 'echo after-refusal'}},
    {...context, toolCallId: 'after-refusal', toolName: 'exec'});
  assert.equal(retried.blockReason, english);
  const delivered = guard.deliveryVerificationForRun(context.runId);
  assert.match(delivered.text, /Portal blocked an unapproved recursive deletion/);
  // By default both checks give the reason unchanged.
  const plain = createToolLoopGuard({abortRun: () => {}});
  plain.observeRun(context, 'pixel', {prompt: 'Analise as limitações do workspace e explique quais recursos estão disponíveis.'});
  for (const command of ['rm -rf /tmp/probe_venv', 'echo after-refusal']) {
    assert.equal(plain.beforeToolCall({toolName: 'exec', params: {command}}, context).blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  }
});

// The guard's own completion assurance: the citation check reads 05/11 in an
// answer as 5 November for a Portuguese request, but as May 11 in English-only
// mode, where the answer is English.
test('the guard reads answer dates month-first in English-only mode', async () => {
  for (const [language, portuguese] of [['en', false], [undefined, true]]) {
    const seen = [];
    const guard = createToolLoopGuard({responseLanguage: language, hostCitationVerifier: {
      allowed: () => true,
      verify: async request => { seen.push(request.portuguese); return {fetched: 0, verified: [], elapsedMs: 0}; },
    }});
    const context = {agentId: 'pixel', runId: `dates-${language}`, sessionId: `dates-${language}`, sessionKey: `agent:pixel:dates-${language}`};
    guard.observeRun(context, 'pixel', {prompt: 'Pesquise na internet eventos em Lisboa e abra as fontes antes de citar.'});
    const url = 'https://example.org/agenda';
    guard.afterToolCall({toolName: 'web_fetch', toolCallId: 'read', params: {url}, result: {content: [{type: 'text', text: `Fetched ${url}`}],
      details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}}}, {...context, toolName: 'web_fetch', toolCallId: 'read'});
    await guard.verifyCitedPages({lastAssistantMessage: 'O evento é em 05/11/2026 (https://example.org/evento).'}, context);
    assert.deepEqual(seen, [portuguese], String(language));
  }
});

// dashboard-api builds the owner message of team and goal runs. Through the
// real plugin entry: the setting file decides, as on an installed gateway.
const OWNER_WORDS = {goal: 'Crie um site simples sobre café e publique.', context: 'Conversa anterior sobre o site.',
  question: 'Qual cor você prefere?', answer: 'Use azul.', title: 'Criar o site'};

test('team and goal turns: English-only replaces the language note with the reminder, never both',
  {skip: !PYTHON_AVAILABLE && 'needs Python'}, async t => {
  const {prompts, answers} = dashboardApiTurns(OWNER_WORDS);
  // The clarification-answer turns too. A Builder's is the owner's answer
  // alone; the team message is earlier in the session.
  const turns = [...Object.entries(prompts), ['reviewer-answer', answers.reviewer], ['goal-answer', answers.goal],
    ['builder-answer', answers.builder, [{role: 'user', content: prompts.builder}, {role: 'assistant', content: OWNER_WORDS.question}]]];
  const noteFor = kind => kind.startsWith('goal') ? GOAL_TURN_LANGUAGE_NOTE : kind === 'coordinator' ? '' : TEAM_TURN_LANGUAGE_NOTE;
  const plain = await realPromptBuild(t);
  for (const [kind, prompt, messages = []] of turns) {
    assert.doesNotMatch(prompt, /language|English/i, `${kind}: the owner message leaves the language to Pixel's rule`);
    const built = await plain(prompt, messages);
    const note = noteFor(kind);
    assert.equal(built.appendContext, besideOwner(kind.startsWith('goal') ? GOAL_CONTRACT : '', note),
      `${kind}: by default the note, exactly once`);
    assert.ok(!built.appendContext?.includes(ENGLISH_TURN_REMINDER), kind);
    assert.equal(built.appendSystemContext.split(RESPONSE_LANGUAGE_CONTRACT).length, kind === 'coordinator' ? 1 : 2, `${kind}: one rule`);
  }
  const english = await realPromptBuild(t, {stateFiles: {[RESPONSE_LANGUAGE_FILE]: 'en\n'}});
  for (const [kind, prompt, messages = []] of turns) {
    const built = await english(prompt, messages);
    assert.equal(built.appendContext,
      besideOwner(kind.startsWith('goal') ? inResponseLanguage(GOAL_CONTRACT, 'en') : '', ENGLISH_TURN_REMINDER),
      `${kind}: English-only, the reminder and no note`);
    const seen = [built.prependContext, prompt, built.appendContext, built.appendSystemContext].filter(Boolean).join('\n');
    assert.ok(!seen.includes(TEAM_TURN_LANGUAGE_NOTE) && !seen.includes(GOAL_TURN_LANGUAGE_NOTE), kind);
    assert.doesNotMatch(seen, /owner(?:'s)? language|reply language|language of the|same language as/i,
      `${kind}: no fixed text points to the request's language`);
    assert.ok(!built.appendSystemContext.includes(RESPONSE_LANGUAGE_CONTRACT), kind);
    assert.equal(built.appendSystemContext.includes(ENGLISH_RESPONSE_CONTRACT), kind !== 'coordinator', kind);
  }
  // An ordinary turn adds a language reminder only in English-only mode.
  assert.equal((await plain('Create a nice web page for me')).appendContext, PORTAL_INTEGRATION_TURN_CONTEXT);
  assert.equal((await english('Create a nice web page for me')).appendContext, besideOwner(ENGLISH_TURN_REMINDER));
});
