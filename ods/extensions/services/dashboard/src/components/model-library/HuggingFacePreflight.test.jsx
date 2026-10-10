import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {afterEach, beforeEach, expect, test, vi} from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

const repo = {id: 'org/model', author: 'org'}
const artifacts = [
  {id: 'q4', label: 'model-Q4_K_M.gguf', quantization: 'Q4_K_M', sizeBytes: 4e9, files: [{}]},
  {id: 'q8', label: 'model-Q8_0.gguf', quantization: 'Q8_0', sizeBytes: 8e9, files: [{}]},
]
const okCheck = {
  fit: {status: 'fits', contextLength: 65536, requiredGb: 6.2, capacityGb: 8, estimate: 'architecture'},
  disk: 'ok',
}

function preflight(overrides = {}) {
  return {
    id: 'org/model',
    sha: 'c'.repeat(40),
    header: {status: 'read', file: 'model-Q4_K_M.gguf', bytesRead: 2000000},
    architecture: 'qwen3',
    runtime: {key: 'nvidia', build: 'b11429', architectureSupported: true},
    modelKind: 'chat',
    contextLength: 40960,
    contextSource: 'gguf_header',
    template: {present: true, tools: true, thinking: 'toggle'},
    storage: {freeBytes: 1e11, totalBytes: 1e12, marginBytes: 5e10},
    artifacts: {q4: okCheck, q8: {...okCheck, fit: {...okCheck.fit, status: 'too_large'}}},
    refusal: null,
    ...overrides,
  }
}

let preflightBody
let detailsBody
let importResponse
beforeEach(() => {
  vi.useFakeTimers()
  preflightBody = preflight()
  detailsBody = {...repo, artifacts}
  importResponse = {ok: true, status: 200, body: {modelId: 'hf-fixture', status: 'downloading'}}
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url.includes('/search?')) return {ok: true, status: 200, json: async () => ({models: [repo]})}
    if (url.includes('/preflight/')) return {ok: true, status: 200, json: async () => preflightBody}
    if (url.endsWith('/import')) {
      const {ok, status, body} = importResponse
      return {ok, status, headers: {get: name => (name === 'X-ODS-Import-Started' ? 'false' : null)}, json: async () => body}
    }
    return {ok: true, status: 200, json: async () => detailsBody}
  }))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

async function open(buttonName = 'Choose file') {
  render(<HuggingFaceModelBrowser/>)
  await act(async () => { vi.advanceTimersByTime(350) })
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: buttonName})) })
  return within(screen.getByRole('dialog'))
}

const importBodies = () => fetch.mock.calls
  .filter(([url]) => url.endsWith('/import'))
  .map(([, options]) => JSON.parse(options.body))

test('shows what the repository’s files say before any download', async () => {
  const dialog = await open()
  const summary = dialog.getByRole('region', {name: 'Before you download'})
  expect(summary).toHaveTextContent('Architecture qwen3 runs on this machine’s llama.cpp (b11429).')
  expect(summary).toHaveTextContent('Its template describes tool calls · Thinking can be turned off')
  expect(dialog.getByText('Fits at 64K tokens')).toBeVisible()
  expect(dialog.getByText('Too large')).toBeVisible()
  expect(dialog.getByText('40K tokens')).toBeVisible()
  expect(fetch.mock.calls.filter(([url]) => url.includes('/preflight/'))).toHaveLength(1)
})

test('a non-chat model cannot be imported, and the reason links to help', async () => {
  preflightBody = preflight({
    modelKind: 'reranker',
    runtime: {key: 'nvidia', build: 'b11429', architectureSupported: null},
    refusal: {code: 'not_a_chat_model:reranker', message: 'This is a reranking model.', overridable: false},
  })
  const dialog = await open()
  const alert = dialog.getByRole('alert')
  expect(alert).toHaveTextContent('This is a reranking model.')
  expect(within(alert).getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
  for (const button of dialog.getAllByRole('button', {name: /Not supported/})) expect(button).toBeDisabled()
})

test('an unsupported architecture needs an explicit Import anyway', async () => {
  preflightBody = preflight({
    architecture: 'nanbeige',
    runtime: {key: 'amd', build: 'b9014', architectureSupported: false},
    refusal: {code: 'runtime_architecture_unsupported', message: 'This model needs a newer llama.cpp than this machine runs (build b9014).', overridable: true},
  })
  const dialog = await open()
  const [first] = dialog.getAllByRole('button', {name: 'Import anyway…'})
  await act(async () => { fireEvent.click(first) })
  expect(importBodies()).toHaveLength(0)
  const confirm = dialog.getByRole('alertdialog', {name: 'Import anyway'})
  expect(confirm).toHaveTextContent('Loading it will most likely fail')
  await act(async () => { fireEvent.click(within(confirm).getByRole('button', {name: 'Import anyway'})) })
  expect(importBodies()).toEqual([{repoId: 'org/model', artifactId: 'q4', allowUnsupportedRuntime: true}])
})

test('an artifact without room on the model store is not importable', async () => {
  preflightBody = preflight({artifacts: {q4: {...okCheck, disk: 'insufficient'}, q8: okCheck}})
  const dialog = await open()
  expect(dialog.getByRole('button', {name: 'Not enough disk'})).toBeDisabled()
  expect(dialog.getByText('Not enough free disk space')).toBeVisible()
  expect(dialog.getByRole('button', {name: 'Import', exact: true})).toBeEnabled()
})

test('a failed check never blocks an import', async () => {
  preflightBody = {status: 'idle'}
  const dialog = await open()
  expect(dialog.getByText(/Could not check this repository before download/)).toBeVisible()
  expect(dialog.getAllByRole('button', {name: 'Import', exact: true})).toHaveLength(2)
})

test('a slow check says so in its own words and leaves the import available', async () => {
  const pending = new Promise(() => {})
  const answer = fetch.getMockImplementation()
  fetch.mockImplementation(async (url, options) => (url.includes('/preflight/') ? pending : answer(url, options)))
  const dialog = await open()
  await act(async () => { vi.advanceTimersByTime(30000) })
  expect(dialog.getByText(/Checking this repository before download/)).toBeVisible()
  await act(async () => { vi.advanceTimersByTime(60000) })
  expect(dialog.getByText(/Could not check this repository before download \(the check took longer than 90 seconds\)/)).toBeVisible()
  expect(dialog.queryByText(/Check download status/)).toBeNull()
  expect(dialog.getAllByRole('button', {name: 'Import', exact: true})).toHaveLength(2)
})

test('a server-side runtime refusal offers Import anyway once', async () => {
  preflightBody = {status: 'idle'}
  importResponse = {ok: false, status: 422, body: {detail: {
    code: 'runtime_architecture_unsupported',
    message: 'This model needs a newer llama.cpp than this machine runs (build b9014).',
    overridable: true,
  }}}
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[0]) })
  expect(dialog.getByText(/needs a newer llama.cpp/)).toBeVisible()
  expect(dialog.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
  importResponse = {ok: true, status: 200, body: {modelId: 'hf-fixture', status: 'downloading'}}
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import anyway'})) })
  expect(importBodies()).toEqual([
    {repoId: 'org/model', artifactId: 'q4'},
    {repoId: 'org/model', artifactId: 'q4', allowUnsupportedRuntime: true},
  ])
})

test('a gated repository says how to get access, once, and cannot be imported yet', async () => {
  preflightBody = preflight({
    header: {status: 'unavailable', file: 'model-Q4_K_M.gguf', reason: 'gated', message: 'This repository is gated'},
    template: {present: null, tools: null, thinking: 'unknown'},
    refusal: {code: 'gated', message: 'This model is gated on Hugging Face. Accept its license.', overridable: false},
  })
  const dialog = await open()
  const alert = dialog.getByRole('alert')
  expect(alert).toHaveTextContent('This model is gated on Hugging Face. Accept its license.')
  expect(within(alert).getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
  expect(dialog.queryByText(/Some checks could not run/)).toBeNull()
  for (const button of dialog.getAllByRole('button', {name: /Needs access/})) expect(button).toBeDisabled()
  expect(dialog.queryByRole('button', {name: /^Import/})).toBeNull()
})

test('a repository ODS does not run as a chat model links to help without a pre-download check', async () => {
  repo.runtimeCompatible = false
  detailsBody = {...repo, artifacts, runtimeCompatible: false, runtimeReason: 'Text Ranking requires a dedicated ODS runtime'}
  try {
    const dialog = await open('Inspect')
    expect(dialog.getByText(/ODS will not route it through the LLM runtime/)).toBeVisible()
    expect(dialog.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
    expect(fetch.mock.calls.filter(([url]) => url.includes('/preflight/'))).toHaveLength(0)
  } finally {
    delete repo.runtimeCompatible
  }
})

test('the context label names where the number came from', async () => {
  detailsBody = {...repo, artifacts, contextLength: 32768, contextSource: 'gguf_metadata'}
  preflightBody = preflight({contextLength: 32768, contextSource: 'gguf_metadata', header: {status: 'unavailable', reason: 'rate_limited', message: 'rate limited'}})
  const dialog = await open()
  expect(dialog.getByText('GGUF metadata')).toBeVisible()
  expect(dialog.queryByText('This file’s GGUF header')).toBeNull()
})
