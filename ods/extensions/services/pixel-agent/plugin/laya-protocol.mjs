// Portal-owned contract for Laya's typed decisions. No execution or authority
// is delegated to the classifier. Wire format reviewed against upstream
// NandhaKishorM/laya a4a8921afebfd852bba0000475cfb6ab737a124c.
export const LAYA_LIMITS = Object.freeze({
  items: 32, questions: 8, choices: 20, levels: 20,
  textChars: 20_000, instructionChars: 1200, labelChars: 400,
  requestBytes: 128_000, responseBytes: 256_000,
});

export class LayaProtocolError extends Error {
  constructor(code, message) { super(message); this.name = 'LayaProtocolError'; this.code = code; }
}

function reject(code, message) { throw new LayaProtocolError(code, message); }
function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}
function fields(value, allowed, required = []) {
  if (!record(value) || Object.keys(value).some(key => !allowed.includes(key))
      || required.some(key => !Object.hasOwn(value, key))) {
    reject('invalid_request', 'Use only the documented fields and supply all required fields.');
  }
}
function text(value, max, name) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || value.includes('\0')) {
    reject('invalid_request', `${name} must be nonempty text of at most ${max} characters.`);
  }
  return value;
}
function identifier(value) {
  if (typeof value !== 'string' || !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value)
      || ['__proto__', 'constructor', 'prototype'].includes(value)) {
    reject('invalid_request', 'IDs must be unique simple identifiers, at most 64 characters.');
  }
  return value;
}
function list(value, min, max, name) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    reject('invalid_request', `${name} must contain ${min}–${max} entries.`);
  }
  return value;
}
function unique(values, name) {
  if (new Set(values).size !== values.length) reject('invalid_request', `${name} must be unique.`);
}

function questionWire(question) {
  fields(question, ['id', 'type', 'instructions', 'choices', 'levels'], ['id', 'type', 'instructions']);
  const id = identifier(question.id);
  const instructions = text(question.instructions, LAYA_LIMITS.instructionChars, 'instructions');
  const type = question.type;
  if (!['choice', 'score', 'noul'].includes(type)) reject('invalid_request', 'Unknown decision type.');
  if (type !== 'choice' && Object.hasOwn(question, 'choices')) reject('invalid_request', 'Only choice accepts choices.');
  if (type !== 'score' && Object.hasOwn(question, 'levels')) reject('invalid_request', 'Only score accepts levels.');
  if (type === 'noul') return [id, {type, instructions}];
  if (type === 'score') {
    const levels = list(question.levels, 2, LAYA_LIMITS.levels, `questions.${id}.levels`)
      .map(label => text(label, LAYA_LIMITS.labelChars, 'level'));
    unique(levels, 'levels');
    return [id, {type, instructions, criteria: levels}];
  }
  const choices = list(question.choices, 2, LAYA_LIMITS.choices, `questions.${id}.choices`).map(choice => {
    fields(choice, ['id', 'description'], ['id', 'description']);
    return [identifier(choice.id), text(choice.description, LAYA_LIMITS.labelChars, 'description')];
  });
  unique(choices.map(([key]) => key), 'choice IDs');
  return [id, {type, instructions, criteria: Object.fromEntries(choices)}];
}

// Each item gets an independent state and shares the question definitions.
// No chat history, files, paths, URLs or credentials are fetched implicitly.
export function prepareLayaRequest(input) {
  fields(input, ['items', 'questions', 'language', 'checkpoint', 'contextTokens'], ['items', 'questions']);
  const items = list(input.items, 1, LAYA_LIMITS.items, 'items').map(item => {
    fields(item, ['id', 'text'], ['id', 'text']);
    return {id: identifier(item.id), text: text(item.text, LAYA_LIMITS.textChars, 'item text')};
  });
  unique(items.map(item => item.id), 'item IDs');
  const entries = list(input.questions, 1, LAYA_LIMITS.questions, 'questions').map(questionWire);
  unique(entries.map(([id]) => id), 'question IDs');
  const checkpoint = input.checkpoint ?? 'auto';
  if (!['auto', 'english', 'multilingual', 'typed-decisions'].includes(checkpoint)) {
    reject('invalid_request', 'Unknown checkpoint.');
  }
  if (input.language !== undefined && (typeof input.language !== 'string'
      || !/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(input.language))) {
    reject('invalid_request', 'language must be a language code such as pt, en or pt-BR.');
  }
  const questions = Object.fromEntries(entries);
  if (input.contextTokens !== undefined && ![512, 1024, 2048, 4096, 8192].includes(input.contextTokens)) {
    reject('invalid_request', 'contextTokens must be 512, 1024, 2048, 4096 or 8192.');
  }
  const body = {states: items.map(item => ({text: item.text})), questions, model: checkpoint,
    ...(input.language ? {lang: input.language} : {}),
    ...(input.contextTokens === undefined ? {} : {max_len: input.contextTokens})};
  if (Buffer.byteLength(JSON.stringify(body), 'utf8') > LAYA_LIMITS.requestBytes) {
    reject('request_too_large', 'Split this decision batch into smaller batches; nothing was sent.');
  }
  return {items, questions, body};
}

function invalidResponse() { reject('invalid_response', 'Laya returned an invalid or mismatched decision.'); }
function probability(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) invalidResponse();
  return value;
}
function sameKeys(value, keys) {
  return record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function distribution(value, keys) {
  if (!sameKeys(value, keys)) invalidResponse();
  const pairs = keys.map(key => [key, probability(value[key])]);
  if (Math.abs(pairs.reduce((sum, [, p]) => sum + p, 0) - 1) > 0.002) invalidResponse();
  return Object.fromEntries(pairs);
}
function answerResult(answer, question) {
  if (!record(answer) || answer.type !== question.type) invalidResponse();
  // The upstream action head and arbitrary fields are deliberately not
  // forwarded: confidence is evidence, never permission to execute a tool.
  const result = {type: answer.type};
  if (answer.confidence !== undefined) result.confidence = probability(answer.confidence);
  if (answer.answer_confidence !== undefined) result.answerConfidence = probability(answer.answer_confidence);
  if (answer.type === 'noul') return {...result, probability: probability(answer.noul)};
  if (answer.type === 'choice') {
    const keys = Object.keys(question.criteria);
    if (!keys.includes(answer.choice)) invalidResponse();
    return {...result, choice: answer.choice, probabilities: distribution(answer.probabilities, keys)};
  }
  const keys = question.criteria.map((_, index) => String(index));
  if (!sameKeys(answer.legend, keys) || keys.some(key => answer.legend[key] !== question.criteria[Number(key)])
      || typeof answer.score !== 'number' || !Number.isFinite(answer.score)
      || answer.score < 0 || answer.score > keys.length - 1) invalidResponse();
  return {...result, score: answer.score, levels: [...question.criteria],
    probabilities: distribution(answer.probabilities, keys)};
}

function decisionResult(value, questions) {
  if (!record(value) || !sameKeys(value.answers, Object.keys(questions))) invalidResponse();
  const usage = value.usage;
  if (!record(usage) || typeof usage.truncated !== 'boolean'
      || !Array.isArray(usage.truncated_questions) || !Number.isSafeInteger(usage.state_tokens_dropped)
      || usage.state_tokens_dropped < 0) {
    reject('unverified_context', 'Laya did not report whether it consumed the full decision context.');
  }
  if (usage.truncated || usage.state_tokens_dropped !== 0 || usage.truncated_questions.length) {
    reject('truncated_context', 'Laya truncated the context or question. Shorten or split the input; do not use this decision.');
  }
  if (typeof value.routing?.model !== 'string'
      || !['english', 'multilingual', 'typed-decisions'].includes(value.routing.model)) invalidResponse();
  return {checkpoint: value.routing.model,
    answers: Object.fromEntries(Object.entries(questions).map(([id, question]) => [id, answerResult(value.answers[id], question)]))};
}

export function readLayaResponse(value, prepared) {
  if (!record(value) || !Array.isArray(value.results) || value.results.length !== prepared.items.length) invalidResponse();
  // Upstream's batch API guarantees input order. IDs are bound here, never
  // taken from model output. Partial/malformed batches cannot be mislabelled.
  return {kind: 'laya-decisions', advisory: true, completeContext: true,
    items: value.results.map((result, index) => ({id: prepared.items[index].id,
      ...decisionResult(result, prepared.questions)}))};
}
