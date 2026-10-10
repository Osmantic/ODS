import {act, fireEvent, render, screen, within} from '@testing-library/react'
import {afterEach, beforeEach, expect, test, vi} from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

const repo = {id: 'org/model', author: 'org'}
const artifacts = [
  {id: 'q4', label: 'model-Q4_K_M.gguf', quantization: 'Q4_K_M', sizeBytes: 4e9, files: [{filename: 'model-Q4_K_M.gguf'}]},
  {id: 'q8', label: 'model-Q8_0.gguf', quantization: 'Q8_0', sizeBytes: 8e9, files: [{filename: 'model-Q8_0.gguf'}]},
]
const okCheck = {
  fit: {status: 'fits', contextLength: 65536, requiredGb: 6.2, capacityGb: 8, estimate: 'architecture'},
  disk: 'ok',
}

function preflight(overrides = {}) {
  const body = {
    id: 'org/model',
    sha: 'c'.repeat(40),
    artifactId: 'q4',
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
  body.artifacts = Object.fromEntries(Object.entries(body.artifacts).map(([id, check]) => [id, {
    ...check,
    header: check.header || {status: 'read', file: artifacts.find(row => row.id === id)?.files[0].filename},
    refusal: Object.hasOwn(check, 'refusal') ? check.refusal : body.refusal,
  }]))
  return body
}

const selectedPreflight = (id, overrides = {}) => preflight({
  artifactId: id,
  header: {status: 'read', file: artifacts.find(row => row.id === id)?.files[0].filename},
  ...overrides,
})

let preflightBody
let detailsBody
let importResponse
let selectedPreflightResponse
beforeEach(() => {
  vi.useFakeTimers()
  preflightBody = preflight()
  detailsBody = {...repo, sha: 'c'.repeat(40), artifacts}
  selectedPreflightResponse = id => selectedPreflight(id)
  importResponse = {ok: true, status: 200, body: {modelId: 'hf-fixture', status: 'downloading'}}
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url.includes('/search?')) return {ok: true, status: 200, json: async () => ({models: [repo]})}
    if (url.includes('/preflight/')) {
      const id = new URL(url, 'https://ods.test').searchParams.get('artifactId')
      return {ok: true, status: 200, json: async () => id ? selectedPreflightResponse(id) : preflightBody}
    }
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
  expect(importBodies()).toEqual([{repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4', allowUnsupportedRuntime: true}])
})

test('an artifact without room on the model store is not importable', async () => {
  preflightBody = preflight({artifacts: {q4: {...okCheck, disk: 'insufficient'}, q8: okCheck}})
  const dialog = await open()
  expect(dialog.getByRole('button', {name: 'Not enough disk'})).toBeDisabled()
  expect(dialog.getByText('Not enough free disk space')).toBeVisible()
  expect(dialog.getByRole('button', {name: 'Import', exact: true})).toBeEnabled()
})

test('a failed repository check leaves an artifact-specific check available', async () => {
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

test('an initial response from another revision is discarded before selecting an artifact', async () => {
  preflightBody = preflight({sha: 'd'.repeat(40), architecture: 'wrong-revision'})
  const dialog = await open()
  expect(dialog.getByText(/Could not check this repository before download/)).toBeVisible()
  expect(dialog.queryByText(/wrong-revision/)).toBeNull()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[0]) })
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q4'))).toHaveLength(1)
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q4'}])
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
    {repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4'},
    {repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4', allowUnsupportedRuntime: true},
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
  detailsBody = {...repo, sha: 'c'.repeat(40), artifacts, runtimeCompatible: false, runtimeReason: 'Text Ranking requires a dedicated ODS runtime'}
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
  detailsBody = {...repo, sha: 'c'.repeat(40), artifacts, contextLength: 32768, contextSource: 'gguf_metadata'}
  preflightBody = preflight({contextLength: 32768, contextSource: 'gguf_metadata', header: {status: 'unavailable', file: 'model-Q4_K_M.gguf', reason: 'rate_limited', message: 'rate limited'}})
  const dialog = await open()
  expect(dialog.getByText('GGUF metadata')).toBeVisible()
  expect(dialog.queryByText('This file’s GGUF header')).toBeNull()
})

test('a file stored in a tensor type this runtime cannot read needs an explicit Import anyway, other files do not', async () => {
  const refusal = {code: 'runtime_tensor_type_unsupported', message: 'This file stores weights as Q2_0, which this machine’s llama.cpp (build b9014) cannot read.', overridable: true}
  preflightBody = preflight({
    artifacts: {
      q4: {...okCheck, tensors: {status: 'ok', unknown: [], source: 'header'}},
      q8: {...okCheck, tensors: {status: 'unsupported', unknown: ['Q2_0'], source: 'name', refusal}},
    },
  })
  const dialog = await open()
  expect(dialog.getByText('Stored as Q2_0: needs a newer llama.cpp')).toBeVisible()
  expect(dialog.getByRole('button', {name: /^Import$/})).toBeEnabled()
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import anyway…'})) })
  expect(dialog.getByRole('alertdialog', {name: 'Import anyway'})).toHaveTextContent('stores weights as Q2_0')
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import anyway'})) })
  expect(importBodies()).toEqual([{repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q8', allowUnsupportedRuntime: true}])
})

test('a repository with a vision projector imports it unless Include vision is unticked', async () => {
  detailsBody = {
    ...repo, sha: 'c'.repeat(40), artifacts,
    projectors: [{id: 'p1', label: 'mmproj-F16.gguf', sizeBytes: 9e8, precision: 'F16'}],
    defaultProjectorId: 'p1',
  }
  const dialog = await open()
  const vision = dialog.getByRole('checkbox', {name: /Include vision/})
  expect(vision).toBeChecked()
  expect(vision.closest('label')).toHaveTextContent('mmproj-F16.gguf')
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: /^Import$/})[0]) })
  expect(importBodies()[0]).toEqual({repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4', includeVision: true})
})

test('unticking Include vision imports the weights alone', async () => {
  detailsBody = {
    ...repo, sha: 'c'.repeat(40), artifacts,
    projectors: [{id: 'p1', label: 'mmproj-F16.gguf', sizeBytes: 9e8, precision: 'F16'}],
    defaultProjectorId: 'p1',
  }
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getByRole('checkbox', {name: /Include vision/})) })
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: /^Import$/})[0]) })
  expect(importBodies()[0]).toEqual({repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4', includeVision: false})
})

test('a repository without a projector shows no vision choice', async () => {
  const dialog = await open()
  expect(dialog.queryByRole('checkbox', {name: /Include vision/})).toBeNull()
})

test('a runtime that cannot load vision says why and imports the weights alone', async () => {
  detailsBody = {
    ...repo, sha: 'c'.repeat(40), artifacts,
    projectors: [{id: 'p1', label: 'mmproj-F16.gguf', sizeBytes: 9e8, precision: 'F16'}],
    defaultProjectorId: null,
    visionUnavailableReason: 'This Windows model runtime was set up before vision support, so the model imports without its vision file. Run Windows setup again to add vision',
  }
  const dialog = await open()
  expect(dialog.queryByRole('checkbox', {name: /Include vision/})).toBeNull()
  expect(dialog.getByText(/set up before vision support/)).toBeInTheDocument()
  expect(dialog.getByText(/set up before vision support/).closest('div').querySelector('a[href^="https://discord.gg/"]')).not.toBeNull()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: /^Import$/})[0]) })
  expect(importBodies()[0]).toEqual({repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q4'})
})

test('a mixed repository scopes checked-file evidence and refusal to its artifact', async () => {
  const refusal = {code: 'not_a_chat_model:reranker', message: 'This is a reranking model.', overridable: false}
  preflightBody = preflight({
    artifactId: 'q4',
    modelKind: 'reranker',
    refusal,
    artifacts: {
      q4: {...okCheck, refusal},
      q8: {
        fit: {...okCheck.fit, estimate: 'rough'}, disk: 'ok', refusal: null,
        header: {status: 'unavailable', file: 'model-Q8_0.gguf', reason: 'not_read'},
      },
    },
  })
  const dialog = await open()
  expect(dialog.getByRole('region', {name: 'Before you download'})).toHaveTextContent('Checks for model-Q4_K_M.gguf.')
  expect(dialog.getByText('Checked artifact context')).toBeVisible()
  expect(dialog.getByRole('button', {name: 'Not supported'})).toBeDisabled()
  expect(dialog.getByText('This artifact’s header has not been checked.')).toBeVisible()
  expect(dialog.getByText('Fits at 64K tokens (rough estimate)')).toBeVisible()
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import', exact: true})) })
  expect(importBodies()).toEqual([{repoId: 'org/model', revision: 'c'.repeat(40), artifactId: 'q8'}])
  expect(fetch.mock.calls.filter(([url]) => url.includes('/preflight/'))).toHaveLength(2)
})

function unreadSecondArtifact() {
  preflightBody = preflight({artifacts: {
    q4: okCheck,
    q8: {...okCheck, header: {status: 'unavailable', file: 'model-Q8_0.gguf', reason: 'not_read'}},
  }})
}

test('unchecked import waits for exactly one selected-artifact check before posting', async () => {
  unreadSecondArtifact()
  detailsBody = {...detailsBody, projectors: [{id: 'p1', label: 'mmproj-F16.gguf', sizeBytes: 9e8}], defaultProjectorId: 'p1'}
  let finish
  selectedPreflightResponse = () => new Promise(resolve => { finish = resolve })
  const dialog = await open()
  const button = dialog.getAllByRole('button', {name: 'Import', exact: true})[1]
  await act(async () => { fireEvent.click(button); fireEvent.click(button) })
  expect(dialog.getByRole('button', {name: 'Checking file…'})).toBeDisabled()
  expect(dialog.getByRole('checkbox', {name: /Include vision/})).toBeDisabled()
  expect(importBodies()).toEqual([])
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q8'))).toHaveLength(1)
  await act(async () => { finish(selectedPreflight('q8')) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8', includeVision: true}])
})

test('matching checked metadata needs no additional network preflight', async () => {
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[0]) })
  expect(fetch.mock.calls.filter(([url]) => url.includes('/preflight/'))).toHaveLength(1)
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q4'}])
})

test.each(['architecture', 'tensor'])('selected unsupported %s waits for explicit override without another check', async kind => {
  unreadSecondArtifact()
  const refusal = {code: kind === 'tensor' ? 'runtime_tensor_type_unsupported' : 'runtime_architecture_unsupported', message: 'This selected file needs a newer runtime.', overridable: true}
  const architectureRefusal = kind === 'architecture' ? refusal : null
  selectedPreflightResponse = id => selectedPreflight(id, {refusal: architectureRefusal, artifacts: {
    q4: {...okCheck, refusal: null}, q8: {...okCheck, refusal: architectureRefusal,
      tensors: kind === 'tensor' ? {status: 'unsupported', unknown: ['Q2_0'], source: 'header', refusal} : undefined},
  }})
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  expect(importBodies()).toEqual([])
  expect(dialog.getByRole('button', {name: 'Import', exact: true})).toBeEnabled()
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import anyway…'})) })
  expect(importBodies()).toEqual([])
  await act(async () => { fireEvent.click(within(dialog.getByRole('alertdialog')).getByRole('button', {name: 'Import anyway'})) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8', allowUnsupportedRuntime: true}])
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q8'))).toHaveLength(1)
})

test('selected non-chat refusal never posts an import', async () => {
  unreadSecondArtifact()
  const refusal = {code: 'not_a_chat_model:reranker', message: 'The selected file is a reranker.', overridable: false}
  selectedPreflightResponse = id => selectedPreflight(id, {refusal, artifacts: {
    q4: {...okCheck, refusal: null}, q8: {...okCheck, refusal},
  }})
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  expect(dialog.getByRole('button', {name: 'Not supported'})).toBeDisabled()
  expect(importBodies()).toEqual([])
})

test('a timed-out check offers retry and never claims a possibly-started download', async () => {
  unreadSecondArtifact()
  selectedPreflightResponse = () => new Promise(() => {})
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  await act(async () => { vi.advanceTimersByTime(90000) })
  expect(importBodies()).toEqual([])
  expect(dialog.getByRole('alert')).toHaveTextContent('No download was requested')
  expect(dialog.queryByText(/host may still be processing/)).toBeNull()
  expect(dialog.queryByRole('button', {name: 'Check download status'})).toBeNull()
  selectedPreflightResponse = id => selectedPreflight(id)
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Retry check'})) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8'}])
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q8'))).toHaveLength(2)
})

test.each(['id', 'sha', 'artifactId', 'file'])('a mismatched selected response (%s) never posts', async field => {
  unreadSecondArtifact()
  selectedPreflightResponse = id => {
    const response = selectedPreflight(id)
    if (field === 'file') response.artifacts[id].header.file = 'different.gguf'
    else response[field] = 'different'
    return response
  }
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  expect(dialog.getByRole('alert')).toHaveTextContent('did not match this repository revision and file')
  expect(importBodies()).toEqual([])
})

test('a closed and reopened dialog ignores the old check without unlocking a newer one', async () => {
  unreadSecondArtifact()
  const completions = []
  selectedPreflightResponse = () => new Promise(resolve => completions.push(resolve))
  let dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  await act(async () => { fireEvent.click(dialog.getByTitle('Close')) })
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Choose file'})) })
  dialog = within(screen.getByRole('dialog'))
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  await act(async () => { completions[0](selectedPreflight('q8')) })
  expect(importBodies()).toEqual([])
  expect(dialog.getByRole('button', {name: 'Checking file…'})).toBeDisabled()
  await act(async () => { completions[1](selectedPreflight('q8')) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8'}])
})

test('unavailable selected metadata requires an explicit unknown-data import', async () => {
  unreadSecondArtifact()
  selectedPreflightResponse = id => selectedPreflight(id, {
    header: {status: 'unavailable', file: 'model-Q8_0.gguf', reason: 'rate_limited'},
    artifacts: {q4: okCheck, q8: {...okCheck, header: {status: 'unavailable', file: 'model-Q8_0.gguf', reason: 'rate_limited'}}},
  })
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  expect(importBodies()).toEqual([])
  expect(dialog.getByRole('alert')).toHaveTextContent('architecture, context and template remain unknown')
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import with unknown metadata'})) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8'}])
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q8'))).toHaveLength(1)
})

test('unknown metadata retains an explicit tensor override only for that selected file', async () => {
  unreadSecondArtifact()
  const header = {status: 'unavailable', file: 'model-Q8_0.gguf', reason: 'rate_limited'}
  const refusal = {code: 'runtime_tensor_type_unsupported', message: 'This file uses an unsupported tensor type.', overridable: true}
  selectedPreflightResponse = id => selectedPreflight(id, {
    header, artifacts: {q4: okCheck, q8: {...okCheck, header, refusal: null,
      tensors: {status: 'unsupported', unknown: ['Q2_0'], source: 'name', refusal}}},
  })
  const dialog = await open()
  await act(async () => { fireEvent.click(dialog.getAllByRole('button', {name: 'Import', exact: true})[1]) })
  expect(importBodies()).toEqual([])
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import anyway…'})) })
  await act(async () => { fireEvent.click(within(dialog.getByRole('alertdialog')).getByRole('button', {name: 'Import anyway'})) })
  expect(importBodies()).toEqual([])
  await act(async () => { fireEvent.click(dialog.getByRole('button', {name: 'Import with unknown metadata'})) })
  expect(importBodies()).toEqual([{repoId: repo.id, revision: 'c'.repeat(40), artifactId: 'q8', allowUnsupportedRuntime: true}])
  expect(fetch.mock.calls.filter(([url]) => url.includes('artifactId=q8'))).toHaveLength(1)
})

test('artifact-bound unknown context does not fall back to an unrelated Hub value', async () => {
  detailsBody = {...repo, sha: 'c'.repeat(40), artifacts, contextLength: 131072, contextSource: 'gguf_metadata'}
  preflightBody = preflight({artifactId: 'q4', contextLength: null, contextSource: 'unavailable'})
  const dialog = await open()
  expect(dialog.getByText('Checked artifact context')).toBeVisible()
  expect(dialog.queryByText('128K tokens')).toBeNull()
})

test('repository access refusal still applies to all artifact rows', async () => {
  const refusal = {code: 'gated', message: 'This model is gated on Hugging Face. Accept its license.', overridable: false}
  preflightBody = preflight({
    artifactId: 'q4', refusal,
    artifacts: {q4: {...okCheck, refusal}, q8: {...okCheck, refusal}},
  })
  const dialog = await open()
  expect(dialog.getAllByRole('button', {name: 'Needs access'})).toHaveLength(2)
  for (const button of dialog.getAllByRole('button', {name: 'Needs access'})) expect(button).toBeDisabled()
  expect(dialog.getAllByRole('alert')).toHaveLength(1)
})
