// Portal speaks English by default and answers in another language only when
// the owner writes in it. No built-in text the model or owner sees may be
// Portuguese or steer toward it; Portuguese owner input is still understood.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {GOAL_TURN_LANGUAGE_NOTE, managedTurnOwnerWords, RESPONSE_LANGUAGE_CONTRACT,
  TEAM_TURN_LANGUAGE_NOTE} from '../plugin/response-language.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {ACTIVITY_CONTRACT} from '../plugin/activity-display.mjs';
import {GOAL_CONTRACT} from '../plugin/goal-progress.mjs';
import {STOP_SYNTHESIS_INSTRUCTION, STOP_SYNTHESIS_NOTE} from '../plugin/stop-synthesis.mjs';
import {createCompletionAssurance, executionContext, portugueseDateHint, UNREAD_SOURCE_MARKER,
  UNREAD_SOURCE_NOTE, UNREAD_SOURCES_REPLACEMENT} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard, managedTeamRole, RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {dashboardApiTurns, dashboardContinueGoal, promptHookAlone, PYTHON_AVAILABLE,
  realPromptBuild} from './prompt-build-harness.mjs';

const SERVICES = fileURLToPath(new URL('../../', import.meta.url));
const PLUGIN = path.join(SERVICES, 'pixel-agent', 'plugin');
// Portuguese letters, or common Portuguese words, unaccented UI words included.
// Words that are also English (qual, UMA) are left out. JavaScript's \b only
// knows ASCII letters, so it cannot end a word on an accented one: the
// boundaries here are Unicode letters and digits. The dashboard's
// englishUiText.test.js uses the same detector.
const PORTUGUESE_WORDS = ['não', 'nao', 'você', 'voce', 'fontes?', 'arquivos?', 'verificação', 'verificada', 'consulte',
  'pesquise', 'pesquisando', 'crie', 'publique', 'publicar', 'inspecione', 'quais', 'resultados', 'execução',
  'leitura', 'tarefa', 'ação', 'ações', 'recuperação', 'tentativas', 'bloqueou', 'remoção', 'prévia', 'projeto', 'agentes',
  'seu', 'sua', 'está', 'são', 'escreva', 'responda', 'resposta', 'idioma', 'pedido', 'portugues', 'carregando',
  'aguarde', 'concluido', 'erro', 'salvar', 'enviar', 'cancelar', 'tente novamente'];
const PORTUGUESE = new RegExp(`[ãõçáéíóúâêôàÃÕÇÁÉÍÓÚÂÊÔÀ]|(?<![\\p{L}\\p{N}])(?:${PORTUGUESE_WORDS.join('|')})(?![\\p{L}\\p{N}])`, 'iu');

test('the Portuguese detector catches accented and unaccented Portuguese, and no ordinary English', () => {
  for (const text of ['Página indisponível', 'Concluído', 'Olá! Tudo pronto.', 'O site está pronto.', 'Pesquisando na web',
    'Publicar o site', 'Carregando...', 'Aguarde', 'Concluido', 'Erro de rede', 'Salvar', 'Enviar', 'Cancelar',
    'Tente novamente', 'Escreva no idioma do pedido.', 'Não consegui executar a ação solicitada.']) {
    assert.match(text, PORTUGUESE, text);
  }
  for (const text of ['Loading...', 'Please wait', 'Completed', 'Error: try again', 'GatewayClientRequestError', 'errors',
    'Save', 'Send', 'Cancel', 'Cancelled', 'Publishing the project preview.', 'Consulting sources on the web.',
    "Search the internet for today's news about NVIDIA and cite sources.", 'Configure the backup schedule for my files.',
    'Check which VC firms invested in Osmantic.', 'Procure two quotes for RTX PRO 6000 cards.',
    'Summarize the qual results from the last bench run.', 'Set the UMA frame buffer to 512 MB.',
    'Portal blocked an unapproved recursive deletion. The blocked command did not run; earlier tool activity may have completed.',
    UNREAD_SOURCES_REPLACEMENT, UNREAD_SOURCE_NOTE, RESPONSE_LANGUAGE_CONTRACT, executionContext(),
    TEAM_TURN_LANGUAGE_NOTE, GOAL_TURN_LANGUAGE_NOTE]) {
    assert.doesNotMatch(text, PORTUGUESE, text);
  }
  // A listed word inside a longer word is no match, also next to a letter
  // outside ASCII. With \b these made-up words would match, because \b
  // treats ñ and ü as word breaks.
  for (const text of ['Señerro', 'Grüseu']) assert.doesNotMatch(text, PORTUGUESE, text);
});

// Quoted string literals of a module, without comments or regex literals.
function stringLiterals(src) {
  const out = [];
  let i = 0, line = 1, last = '';
  const regexMayFollow = /[(,=:[!&|?{};+\-*%<>~^]$|^$|\b(?:return|typeof|case|in|of|delete|void|throw|new|else|do)$/;
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (c === '\n') { line++; i++; continue; }
    if (/\s/.test(c)) { i++; continue; }
    if (c === '/' && n === '/') { while (i < src.length && src[i] !== '\n') i++; continue; }
    if (c === '/' && n === '*') {
      const end = src.indexOf('*/', i + 2);
      line += src.slice(i, end).split('\n').length - 1; i = end + 2; continue;
    }
    if (c === '/' && regexMayFollow.test(last)) {
      let k = i + 1, inClass = false;
      for (; k < src.length && src[k] !== '\n'; k++) {
        if (src[k] === '\\') { k++; continue; }
        if (src[k] === '[') inClass = true;
        else if (src[k] === ']') inClass = false;
        else if (src[k] === '/' && !inClass) break;
      }
      for (k++; /[a-z]/i.test(src[k] ?? ''); k++);
      last = 'regex'; i = k; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      const start = line;
      let k = i + 1, text = '';
      while (k < src.length && src[k] !== c) {
        if (src[k] === '\\') { text += src.slice(k, k + 2); k += 2; continue; }
        if (c === '`' && src[k] === '$' && src[k + 1] === '{') {
          let depth = 1;
          for (k += 2; k < src.length && depth; k++) {
            if (src[k] === '{') depth++; else if (src[k] === '}') depth--;
            if (src[k] === '\n') line++;
          }
          text += '${}'; continue;
        }
        if (src[k] === '\n') line++;
        text += src[k++];
      }
      out.push({line: start, text});
      last = 'string'; i = k + 1; continue;
    }
    const word = /^[A-Za-z_$][\w$]*/.exec(src.slice(i, i + 64));
    if (word) { last = word[0]; i += word[0].length; continue; }
    last = c === ')' || c === ']' ? 'close' : c; i++;
  }
  return out;
}

// Python source lines outside full-line comments. Docstrings are scanned too.
function pythonLines(src) {
  return src.split('\n').map((text, index) => ({line: index + 1, text})).filter(({text}) => !text.trim().startsWith('#'));
}

// Every source whose fixed text reaches the model or the owner: the plugin, the
// host ingress and host services (their results and errors), the Edge, and the
// dashboard-api prompts for team and goal runs.
function sweptSources() {
  const listed = (dir, pattern) => fs.readdirSync(path.join(SERVICES, dir)).filter(name => pattern.test(name))
    .map(name => `${dir}/${name}`);
  return [
    ...listed('pixel-agent/plugin', /\.(?:mjs|js)$/).map(file => [file, stringLiterals]),
    ...listed('pixel-agent/host', /\.mjs$/).map(file => [file, stringLiterals]),
    ...listed('pixel-agent/host', /\.py$/).map(file => [file, pythonLines]),
    ...listed('pixel-agent/plugin', /\.py$/).map(file => [file, pythonLines]),
    ...listed('pixel-edge', /\.py$/).map(file => [file, pythonLines]),
    ['dashboard-api/pixel_agent_teams.py', pythonLines],
    ['dashboard-api/portal_goal.py', pythonLines],
  ];
}

// Owner-input classifiers only: RegExp sources, stopword sets, a number-word
// table and an HTML entity table. They read what the owner wrote and never
// become output. An entry of up to four characters must be the whole literal;
// a longer one may be part of it.
const CLASSIFIER_LITERALS = {
  'pixel-agent/plugin/citation-verification.mjs': ['local fonte fontes horario'],
  'pixel-agent/plugin/page-excerpt.mjs': ['que para com por uma'],
  'pixel-agent/plugin/requested-literals.mjs': ['|exatamente(?:', '|dizendo|', 'precisa(?:m)?|tem',
    '|tagline|banner|footer|button|caption|', 'titles?|headings?|headlines?|h[1-6]|buttons?', 'files?|file\\s*names?|',
    'n[ãa]o|nunca|sem|evite', 'por\\\\s+exemplo', 'p[áa]gina|documento|aba|navegador', 'heading|t[íi]tulo', 'acess[íi]vel',
    'três', 'bot[ãa]o|bot[õo]es', 'cards?|events?|items?|products?', 'cart[õo]es|cart[ãa]o', 'opcional(?:mente)?|talvez',
    ...'áÁàÀâÂãÃçÇéÉêÊíÍóÓôÔõÕúÚ'],
  'pixel-agent/plugin/tool-loop-guard.mjs': ['nao\\s+(?:precisa|precisamos', '(?:publicar|republicar|mostrar',
    'read|search|find|check|list|summari[sz]e|what|show|consult[ae]|busqu[ae]|leia|ler|liste|resuma|quais|qual|mostre|verifique|confira|veja|cheque|procure|encontre|pesquise'],
  'dashboard-api/pixel_agent_teams.py': ['revis[aã]o independente'],
};
const classifierEntry = (file, text) => (CLASSIFIER_LITERALS[file] ?? [])
  .find(fragment => fragment.length <= 4 ? text === fragment : text.includes(fragment));

test('built-in text has no Portuguese outside owner-input classifiers', () => {
  const unexpected = [], used = new Set();
  for (const [file, literals] of sweptSources()) {
    for (const literal of literals(fs.readFileSync(path.join(SERVICES, file), 'utf8'))) {
      if (!PORTUGUESE.test(literal.text)) continue;
      const allowed = classifierEntry(file, literal.text);
      if (allowed) used.add(`${file}:${allowed}`);
      else unexpected.push(`${file}:${literal.line}: ${literal.text.slice(0, 120)}`);
    }
  }
  assert.deepEqual(unexpected, [], 'built-in text must be English; classifiers belong in CLASSIFIER_LITERALS');
  const stale = Object.entries(CLASSIFIER_LITERALS).flatMap(([file, fragments]) =>
    fragments.filter(fragment => !used.has(`${file}:${fragment}`)).map(fragment => `${file}:${fragment}`));
  assert.deepEqual(stale, [], 'remove allowlist entries that no longer match');
  const manifest = JSON.stringify(JSON.parse(fs.readFileSync(path.join(PLUGIN, 'openclaw.plugin.json'), 'utf8')));
  assert.doesNotMatch(manifest, PORTUGUESE, 'tool descriptions and config help are English');
  // A long allowlist entry still lets a new Portuguese notice through only if
  // it repeats a classifier's exact RegExp text; a short one never does.
  assert.equal(classifierEntry('pixel-agent/plugin/requested-literals.mjs', 'Conteúdo não verificado'), undefined);
});

test('the whole model-facing context of an English or Portuguese turn is English and carries one language rule', () => {
  for (const options of [{configuredContextWindow: 131072}, {configuredContextWindow: 131072, configuredLeanPrompt: true},
    {configuredContextWindow: 16384}]) {
    for (const executionHost of ['sandbox', 'gateway']) {
      for (const prompt of ['Create a nice web page for me', 'Crie uma página web bonita para mim', 'OK',
        "Identity: Portal\n\nYou are the Reviewer in the owner's Portal team.\nOwner request: crie um site."]) {
        const contract = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt}, {...options, executionHost});
        const system = [ACTIVITY_CONTRACT, GOAL_CONTRACT, contract.appendSystemContext, executionContext()].join(' ');
        assert.doesNotMatch(system, PORTUGUESE, prompt);
        assert.ok(system.includes(RESPONSE_LANGUAGE_CONTRACT), prompt);
        assert.doesNotMatch(system, /owner(?:'s)? language/, 'every language mention defers to the response-language rule');
      }
    }
  }
  for (const text of [createAskUserTool().description, STOP_SYNTHESIS_INSTRUCTION, STOP_SYNTHESIS_NOTE,
    RECURSIVE_DELETE_REQUIRES_OWNER_REASON]) {
    assert.doesNotMatch(text, PORTUGUESE, text);
    assert.doesNotMatch(text, /owner(?:'s)? language/, text);
  }
  assert.match(STOP_SYNTHESIS_INSTRUCTION, /English if it is unclear\), not the language of the excerpts/,
    'the tool-free synthesis call has no other language rule');
});

// English requests that share words with Portuguese (internet, configure, VC,
// procure, qual) once got Portuguese replacement answers.
const ENGLISH_REQUESTS = ["Search the internet for today's news about NVIDIA and cite sources.",
  'Configure the backup schedule for my files.', 'Check which VC firms invested in Osmantic.',
  'Procure two quotes for RTX PRO 6000 cards.', 'Summarize the qual results from the last bench run.',
  'Create a nice web page for me'];
// "Procure" is also the usual Portuguese verb for "search for", so the date
// hint keeps it: only the reading of an ambiguous numeric date follows.
const DAY_FIRST_ENGLISH = new Set(['Procure two quotes for RTX PRO 6000 cards.']);

test('an English request never gets a Portuguese notice, through the guard the owner reads', () => {
  for (const [index, prompt] of ENGLISH_REQUESTS.entries()) {
    const context = {agentId: 'pixel', runId: `english-${index}`, sessionId: `english-${index}`, sessionKey: `agent:pixel:english-${index}`};
    const guard = createToolLoopGuard();
    guard.observeRun(context, 'pixel', {prompt});
    const event = {lastAssistantMessage: 'I will search and configure it now.'};
    for (let attempt = 0; attempt < 3; attempt++) guard.beforeAgentFinalize(event, context);
    const delivered = guard.deliveryVerificationForRun(context.runId)?.text ?? '';
    assert.ok(delivered, `the guard replaces the promise-only answer: ${prompt}`);
    assert.doesNotMatch(delivered, PORTUGUESE, prompt);
    const dayFirst = DAY_FIRST_ENGLISH.has(prompt);
    assert.equal(portugueseDateHint(prompt), dayFirst, prompt);
    // Where the hint is used: the citation check reads the answer's 05/11 as
    // May 11 for these requests, not 5 November (except after "Procure").
    const research = createCompletionAssurance();
    research.begin(`${prompt} Open the sources before citing them.`);
    research.observe('web_search', {result: {details: {results: [{url: 'https://example.org/a'}]}}});
    assert.deepEqual(research.hostVerificationCandidates('The event is on 05/11/2026 (https://example.org/a).'),
      {urls: ['https://example.org/a'], portuguese: dayFirst}, prompt);
  }
  const reads = createCompletionAssurance();
  reads.begin('Search the internet and open the sources before citing them.');
  reads.observe('web_fetch', {result: {details: {status: 200, url: 'https://example.org/read'},
    content: [{type: 'text', text: 'Read page text for the first event, long enough to count.'}]}});
  const answer = 'Event A is on October 3 (https://example.org/read). Event B is on October 4 (https://example.org/unread).';
  reads.finalize(answer);
  assert.doesNotMatch(reads.terminal, PORTUGUESE);
  assert.ok([UNREAD_SOURCES_REPLACEMENT].includes(reads.terminal) || reads.terminal.includes(UNREAD_SOURCE_MARKER));
});

// Portuguese requests whose dates the citation check reads day-first.
const PORTUGUESE_DATE_REQUESTS = ['Pesquise na internet eventos em Lisboa.', 'Procure eventos em Lisboa em novembro.',
  'Quais eventos acontecem em Lisboa em novembro?', 'Qual é a agenda de Lisboa em novembro?'];

test('a Portuguese request keeps its Portuguese answer: nothing translates or overrides it', () => {
  // Default mode adds no English-forcing text: the rule matches the owner's message.
  const contract = promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt: 'Quais são três dicas para manter o foco?'},
    {configuredContextWindow: 131072});
  assert.ok(contract.appendSystemContext.includes(RESPONSE_LANGUAGE_CONTRACT));
  assert.equal(contract.appendContext, undefined);
  // A complete Portuguese answer is delivered as written.
  const done = createCompletionAssurance();
  done.begin('Pesquise na internet as notícias de hoje sobre a NVIDIA e cite as fontes.');
  done.observe('web_search', {result: {details: {results: [{url: 'https://example.org/nvidia'}]}}});
  assert.equal(done.finalize('A NVIDIA anunciou novos drivers hoje (https://example.org/nvidia).'), undefined);
  assert.equal(done.terminal, undefined);
  // A fixed notice is English, appended to the owner's own-language answer.
  assert.match(UNREAD_SOURCE_NOTE, /^\*\*Source check:\*\*/);
  // Portuguese dates are still read day-first, also for a request whose only
  // cue is "Procure", "Quais" or "Qual é".
  for (const prompt of PORTUGUESE_DATE_REQUESTS) {
    assert.equal(portugueseDateHint(prompt), true, prompt);
    // Where the hint is used: the citation check reads 05/11 in the answer to
    // a Portuguese request as 5 November.
    const dates = createCompletionAssurance();
    dates.begin(`${prompt} Abra as fontes antes de citar.`);
    dates.observe('web_search', {result: {details: {results: [{url: 'https://example.org/a'}]}}});
    assert.deepEqual(dates.hostVerificationCandidates('O evento é em 05/11/2026 (https://example.org/a).'),
      {urls: ['https://example.org/a'], portuguese: true}, prompt);
  }
});

// The guard's own citation check hands the verifier the same reading.
test('the guard reads answer dates day-first for a Portuguese request and month-first for an English one', async () => {
  for (const [index, [prompt, portuguese]] of [...PORTUGUESE_DATE_REQUESTS.map(text => [`${text} Abra as fontes antes de citar.`, true]),
    ['Search the internet for events in Lisbon and open the sources before citing them.', false]].entries()) {
    const seen = [];
    const guard = createToolLoopGuard({hostCitationVerifier: {
      allowed: () => true,
      verify: async request => { seen.push(request.portuguese); return {fetched: 0, verified: [], elapsedMs: 0}; },
    }});
    const id = `${portuguese ? 'pt' : 'en'}-${index}`;
    const context = {agentId: 'pixel', runId: `dates-${id}`, sessionId: `dates-${id}`, sessionKey: `agent:pixel:dates-${id}`};
    guard.observeRun(context, 'pixel', {prompt});
    const url = 'https://example.org/agenda';
    guard.afterToolCall({toolName: 'web_fetch', toolCallId: 'read', params: {url}, result: {content: [{type: 'text', text: `Fetched ${url}`}],
      details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}}}, {...context, toolName: 'web_fetch', toolCallId: 'read'});
    await guard.verifyCitedPages({lastAssistantMessage: 'The event is on 05/11/2026 (https://example.org/evento).'}, context);
    assert.deepEqual(seen, [portuguese], prompt);
  }
});

// The unit replay of runtime_blocked_diagnostic.integration.mjs ('tool'): a
// receipt, a blocked recursive delete, then another tool call ends the run.
test('a Portuguese request whose recursive delete was blocked gets the English refusal and excerpt heading', () => {
  const context = {agentId: 'pixel', runId: 'blocked-pt', sessionId: 'session-blocked-pt', sessionKey: 'agent:pixel:blocked-pt'};
  const guard = createToolLoopGuard({abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt: 'Analise as limitações do workspace e explique os recursos disponíveis.'});
  const params = {command: 'node --version'};
  const call = {...context, toolCallId: 'node-version', toolName: 'exec'};
  guard.beforeToolCall({toolName: 'exec', params}, call);
  guard.afterToolCall({toolName: 'exec', params, result: {details: {status: 'completed', exitCode: 0, aggregated: 'v22.23.1'},
    content: [{type: 'text', text: 'v22.23.1'}]}}, call);
  const first = guard.beforeToolCall({toolName: 'exec', params: {command: "python3 -m venv '/tmp/x' && rm -rf '/tmp/x'"}},
    {...context, toolCallId: 'venv', toolName: 'exec'});
  const retried = guard.beforeToolCall({toolName: 'exec', params: {command: 'echo after-refusal'}},
    {...context, toolCallId: 'after', toolName: 'exec'});
  for (const decision of [first, retried]) assert.equal(decision.blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  const {text} = guard.deliveryVerificationForRun(context.runId);
  assert.match(text, /^Portal blocked an unapproved recursive deletion\./);
  assert.match(text, /Earlier received results \(data excerpts; the exit code does not verify every substep\):/);
  assert.match(text, /v22\.23\.1/);
  assert.doesNotMatch(text, PORTUGUESE);
});

// dashboard-api and the dashboard build the owner message of team and goal
// runs; the owner's own words are only a small part of each.
const OWNER_WORDS = {goal: 'Crie um site simples sobre café e publique.', context: 'Conversa anterior sobre o site.',
  question: 'Qual cor você prefere?', answer: 'Use azul.', title: 'Criar o site'};
// A goal the dashboard resumes, or whose question the owner answers.
const GOAL_HISTORY = [{role: 'user', content: `/goal ${OWNER_WORDS.goal}`},
  {role: 'assistant', content: 'Saved partial result.', task: {goal: {status: 'active', summary: 'Working', steps: []}}}];
const quoted = note => [...note.matchAll(/"([^"]+)"/g)].map(match => match[1]);

test("each note names the headings of the owner's words and points the language rule at those words", async () => {
  assert.deepEqual(quoted(TEAM_TURN_LANGUAGE_NOTE), ["Owner's requested outcome:"]);
  assert.deepEqual(quoted(GOAL_TURN_LANGUAGE_NOTE), ['/goal', 'Original objective:']);
  for (const note of [TEAM_TURN_LANGUAGE_NOTE, GOAL_TURN_LANGUAGE_NOTE]) {
    assert.ok(note.startsWith('[ODS language: Portal wrote the instructions in this '), note);
    assert.ok(note.endsWith(" and any owner answers. Apply the response-language rule to those words, not to Portal's instructions.]"),
      note);
  }
  // The dashboard's continueGoal: the objective follows "Original objective:".
  // Its Resume button sends no owner input, and Portal's own text is not
  // labelled as the owner's.
  const continueGoal = await dashboardContinueGoal();
  const [goalPrefix, objectiveHeading] = quoted(GOAL_TURN_LANGUAGE_NOTE);
  for (const answer of [undefined, OWNER_WORDS.answer]) {
    const message = continueGoal(GOAL_HISTORY, 1, answer);
    assert.ok(message.startsWith(`${goalPrefix} `) && message.includes(` ${objectiveHeading} ${OWNER_WORDS.goal}\n`), message);
    assert.equal(managedTurnOwnerWords(message), [OWNER_WORDS.goal, answer].filter(Boolean).join('\n\n'), message);
  }
  assert.equal(managedTurnOwnerWords('Crie um site simples sobre café e publique.'), undefined, 'an ordinary turn');
});

test("team and goal turns carry one note that points the language rule at the owner's own words",
  {skip: !PYTHON_AVAILABLE && 'needs Python'}, async t => {
  const {prompts, answers} = dashboardApiTurns(OWNER_WORDS);
  // A resumed goal reaches Pixel through dashboard-api's goal run.
  const resumed = (await dashboardContinueGoal())(GOAL_HISTORY, 1).replace(/^\/goal /, '');
  const answered = `${OWNER_WORDS.question}\n${OWNER_WORDS.answer}`;
  // A Builder's answer turn is the owner's answer alone; the team message is
  // earlier in the session.
  assert.equal(answers.builder, answered);
  const turns = [...Object.entries(prompts), ['goal-resumed', dashboardApiTurns({...OWNER_WORDS, goal: resumed}).prompts.goal],
    ['builder-answer', answers.builder, [{role: 'user', content: prompts.builder}, {role: 'assistant', content: OWNER_WORDS.question}]],
    ['reviewer-answer', answers.reviewer], ['goal-answer', answers.goal]];
  const [teamHeading] = quoted(TEAM_TURN_LANGUAGE_NOTE), [goalPrefix, objectiveHeading] = quoted(GOAL_TURN_LANGUAGE_NOTE);
  const ownerWords = {coordinator: undefined, 'builder-answer': undefined, 'goal-continued': `${OWNER_WORDS.goal}\n\n${OWNER_WORDS.answer}`,
    'reviewer-answer': `${OWNER_WORDS.goal}\n\n${answered}`, 'goal-answer': `${OWNER_WORDS.goal}\n\n${answered}`};
  const build = await realPromptBuild(t);
  for (const [kind, prompt, messages = []] of turns) {
    const goal = kind.startsWith('goal'), coordinator = kind === 'coordinator';
    // The note's headings are where the owner's words are in these messages.
    if (kind === 'goal-resumed') assert.ok(prompt.startsWith(`${goalPrefix} `) && prompt.includes(` ${objectiveHeading} ${OWNER_WORDS.goal}\n`));
    else if (goal) assert.ok(prompt.startsWith(`${goalPrefix} ${OWNER_WORDS.goal}\n\n`), kind);
    else if (kind === 'reviewer-answer') assert.ok(prompt.includes(`\n\n${teamHeading}\n${OWNER_WORDS.goal}\n\n`), kind);
    else if (kind !== 'builder-answer') assert.equal(prompt.endsWith(`\n\n${teamHeading}\n${OWNER_WORDS.goal}`), !coordinator, kind);
    // Those words alone are the owner request of the stop synthesis.
    assert.equal(managedTurnOwnerWords(prompt, managedTeamRole({prompt, messages})),
      Object.hasOwn(ownerWords, kind) ? ownerWords[kind] : OWNER_WORDS.goal, kind);
    const built = await build(prompt, messages);
    // Exactly once, beside the owner's message; the Coordinator only counts workers.
    const note = goal ? GOAL_TURN_LANGUAGE_NOTE : coordinator ? '' : TEAM_TURN_LANGUAGE_NOTE;
    assert.equal(built.appendContext, [goal ? GOAL_CONTRACT : '', note].filter(Boolean).join('\n\n') || undefined, kind);
    assert.ok(!`${built.prependContext ?? ''}${built.appendSystemContext}`.includes('[ODS language:'), kind);
    assert.equal(built.appendSystemContext.split(RESPONSE_LANGUAGE_CONTRACT).length, coordinator ? 1 : 2, `${kind}: one rule`);
    // Outside the owner's words and the one rule, no fixed text names a language.
    let fixed = [built.prependContext, prompt, built.appendContext, built.appendSystemContext.replace(RESPONSE_LANGUAGE_CONTRACT, '')]
      .filter(Boolean).join('\n');
    for (const words of Object.values(OWNER_WORDS)) fixed = fixed.replaceAll(words, '');
    assert.doesNotMatch(fixed, /\b(?:English|Portuguese|Spanish|French|German|Chinese)\b|owner(?:'s)? language|same language/i, kind);
    assert.doesNotMatch(fixed, PORTUGUESE, kind);
  }
  // An ordinary turn, in either language, gets nothing beside the owner's message.
  for (const prompt of ['Create a nice web page for me', 'Crie uma página web bonita para mim', ENGLISH_REQUESTS[0]]) {
    assert.equal((await build(prompt)).appendContext, undefined, prompt);
  }
});

// The real flow rarely combines a team or goal note with a clarification
// answer note, so the hook runs alone here.
test('beside the owner turn: the goal contract, then the team or goal note, then a clarification note', async () => {
  const note = '[ODS Portal clarification note]';
  const team = "You are the Reviewer in the owner's Portal team. Your assignment: review.\n\nOwner's requested outcome:\nCrie um site.";
  assert.equal((await promptHookAlone({goal: true, clarificationNote: note})).appendContext,
    `${GOAL_CONTRACT}\n\n${GOAL_TURN_LANGUAGE_NOTE}\n\n${note}`);
  assert.equal((await promptHookAlone({prompt: team, clarificationNote: note})).appendContext, `${TEAM_TURN_LANGUAGE_NOTE}\n\n${note}`);
  assert.equal((await promptHookAlone({clarificationNote: note})).appendContext, note);
  assert.equal((await promptHookAlone({goal: true})).appendContext, `${GOAL_CONTRACT}\n\n${GOAL_TURN_LANGUAGE_NOTE}`);
  assert.equal((await promptHookAlone()).appendContext, undefined);
});

test('the prompt-build harness puts HOME, USERPROFILE and OPENCLAW_STATE_DIR back after two loads in one test', async t => {
  const names = ['HOME', 'USERPROFILE', 'OPENCLAW_STATE_DIR'];
  const before = names.map(name => process.env[name]);
  let during;
  await t.test('two loads', async loads => {
    await realPromptBuild(loads);
    await realPromptBuild(loads);
    during = names.map(name => process.env[name]);
  });
  assert.notDeepEqual(during, before);
  assert.deepEqual(names.map(name => process.env[name]), before);
});
