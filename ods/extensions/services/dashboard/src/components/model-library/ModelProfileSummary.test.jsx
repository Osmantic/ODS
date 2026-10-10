import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import {afterEach, beforeEach, expect, test, vi} from 'vitest'
import ModelProfileSummary from './ModelProfileSummary'
import ModelActivationNotice from '../ModelActivationNotice'

function profile(summary, extra = {}) {
  return {
    modelId: 'qwen3.5-9b',
    recordedAt: '2026-10-09T20:00:00Z',
    result: {status: 'complete', facts: {buildInfo: 'b11429-d81235049'}, summary},
    ...extra,
  }
}

const capable = {
  chat: true, tools: true, toolsStreamed: true,
  thinking: {control: 'enable_thinking', separated: true, works: true},
  vision: null, tokensPerSecond: 75.3,
}

let profileBody
let recheckResponse
let templateResponse
beforeEach(() => {
  profileBody = {mode: 'observe', modelId: 'qwen3.5-9b', profile: profile(capable)}
  recheckResponse = {ok: true, status: 200, body: {status: 'recorded'}}
  templateResponse = {ok: true, status: 200, body: {status: 'activated', chatTemplateOverride: 'qwen-tools-fix'}}
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.endsWith('/profile/recheck') && options?.method === 'POST') {
      return {ok: recheckResponse.ok, status: recheckResponse.status, json: async () => recheckResponse.body}
    }
    if (url.endsWith('/chat-template') && options?.method === 'POST') {
      return {ok: templateResponse.ok, status: templateResponse.status, json: async () => templateResponse.body}
    }
    return {ok: true, status: 200, json: async () => profileBody}
  }))
})

const offer = {id: 'qwen-tools-fix', reason: 'Its own template drops tool calls.', active: false, supported: true}
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function show() {
  await act(async () => { render(<ModelProfileSummary modelId="qwen3.5-9b"/>) })
}

test('shows what the running model was measured to do, and on which runtime', async () => {
  await show()
  const region = screen.getByRole('region', {name: 'What this model can do'})
  expect(region).toHaveTextContent('Answers chat')
  expect(region).toHaveTextContent('Calls tools (Pixel and agents)')
  expect(region).toHaveTextContent('Thinking can be turned off')
  expect(region).toHaveTextContent('About 75 tokens/s')
  expect(region).toHaveTextContent('on llama.cpp b11429')
  expect(screen.queryByRole('link', {name: 'Get help on Discord'})).toBeNull()
  expect(fetch).toHaveBeenCalledWith('/api/models/qwen3.5-9b/profile', {cache: 'no-store', signal: expect.any(AbortSignal)})
})

test('a chat-only model says so plainly and links to help', async () => {
  profileBody.profile = profile({...capable, tools: false, thinking: {control: 'always'}})
  await show()
  expect(screen.getByText('No working tool calls: chat only for agents')).toBeVisible()
  expect(screen.getByText('Always thinks before it answers')).toBeVisible()
  expect(screen.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
})

test('a model not measured yet says when it will be', async () => {
  profileBody.profile = null
  await show()
  expect(screen.getByText(/Not checked yet/)).toBeVisible()
})

test('a running model never checked can be checked from its card', async () => {
  // The Portal advisory for a model ODS has not checked points here.
  profileBody.profile = null
  await show()
  profileBody.profile = profile(capable)
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Check again'})) })
  expect(fetch).toHaveBeenCalledWith('/api/models/qwen3.5-9b/profile/recheck', {method: 'POST'})
  await waitFor(() => expect(screen.getByRole('region', {name: 'What this model can do'}))
    .toHaveTextContent('Calls tools (Pixel and agents)'))
})

test('a refused first check is shown in words', async () => {
  profileBody.profile = null
  recheckResponse = {ok: false, status: 409, body: {detail: {error: 'Only the running model can be checked; run it first'}}}
  await show()
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Check again'})) })
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Only the running model can be checked'))
  expect(screen.getByText(/Not checked yet/)).toBeVisible()
})

test('nothing shows when profiles are off or the check is unavailable', async () => {
  profileBody = {mode: 'off', modelId: 'qwen3.5-9b', profile: null}
  const {container} = render(<ModelProfileSummary modelId="qwen3.5-9b"/>)
  await act(async () => {})
  expect(container).toBeEmptyDOMElement()
  fetch.mockImplementationOnce(async () => ({ok: false, status: 503, json: async () => ({detail: 'down'})}))
  const second = render(<ModelProfileSummary modelId="other"/>)
  await act(async () => {})
  expect(second.container).toBeEmptyDOMElement()
})

test('Check again re-measures, and a refusal is shown in words', async () => {
  await show()
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Check again'})) })
  expect(fetch).toHaveBeenCalledWith('/api/models/qwen3.5-9b/profile/recheck', {method: 'POST'})
  recheckResponse = {ok: false, status: 409, body: {detail: {error: 'Only the running model can be checked; run it first'}}}
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Check again'})) })
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Only the running model can be checked'))
})

test('no fixed-template action shows without an exact match from the server', async () => {
  profileBody.profile = profile({...capable, chat: false})
  await show()
  expect(screen.queryByRole('button', {name: 'Try a fixed template'})).toBeNull()
  expect(screen.queryByText(/fixed chat template/)).toBeNull()
})

test('an exact match offers a fixed template, and trying it restarts the model with it', async () => {
  profileBody.profile = profile({...capable, chat: false})
  profileBody.templateOverride = offer
  await show()
  expect(screen.getByText('A fixed chat template is available for this model. Its own template drops tool calls.')).toBeVisible()
  expect(screen.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()

  profileBody.templateOverride = {...offer, active: true}
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Try a fixed template'})) })

  expect(fetch).toHaveBeenCalledWith('/api/models/qwen3.5-9b/chat-template', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({override: 'qwen-tools-fix'}),
  })
  await waitFor(() => expect(screen.getByText('Uses a fixed chat template')).toBeVisible())
  expect(screen.queryByRole('button', {name: 'Try a fixed template'})).toBeNull()
})

test('a refused fixed template is explained in words', async () => {
  profileBody.templateOverride = offer
  templateResponse = {ok: false, status: 409, body: {detail: {
    code: 'chat_template_override_not_matched',
    error: 'This fixed chat template is not for this model. Nothing was changed.',
  }}}
  await show()
  await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Try a fixed template'})) })
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('This fixed chat template is not for this model.'))
})

test('a runtime that cannot use the fixed template says so, with help and no action', async () => {
  profileBody.templateOverride = {...offer, supported: false}
  await show()
  expect(screen.getByText(/cannot use it yet/)).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Try a fixed template'})).toBeNull()
  expect(screen.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
})

test('the activation notice names the first-time check', () => {
  render(<MemoryRouter><ModelActivationNotice value={{active: true, phase: 'profiling', failureCode: null}}/></MemoryRouter>)
  expect(screen.getByText('Checking what this model can do (first time only)')).toBeVisible()
})

test('refreshes a delayed first profile after the model lifecycle settles without rechecking', async () => {
  profileBody.profile = null
  const view = render(<ModelProfileSummary modelId="qwen3.5-9b" lifecycleActive={false}/>)
  await act(async () => {})
  expect(screen.getByText(/Not checked yet/)).toBeVisible()
  view.rerender(<ModelProfileSummary modelId="qwen3.5-9b" lifecycleActive={true}/>)
  await act(async () => {})
  expect(fetch).toHaveBeenCalledTimes(1)
  profileBody.profile = profile(capable)
  view.rerender(<ModelProfileSummary modelId="qwen3.5-9b" lifecycleActive={false}/>)
  await act(async () => {})
  expect(screen.getByText('Calls tools (Pixel and agents)')).toBeVisible()
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
})

test('a replaced model ignores a late previous profile response', async () => {
  let resolveOld
  fetch.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
  const view = render(<ModelProfileSummary modelId="old-model"/>)
  profileBody = {mode: 'observe', modelId: 'new-model', profile: profile({...capable, tools: false}, {modelId: 'new-model'})}
  view.rerender(<ModelProfileSummary modelId="new-model"/>)
  await act(async () => {})
  expect(screen.getByText('No working tool calls: chat only for agents')).toBeVisible()
  await act(async () => { resolveOld({ok: true, json: async () => ({mode: 'observe', modelId: 'old-model', profile: profile(capable, {modelId: 'old-model'})})}) })
  expect(screen.getByText('No working tool calls: chat only for agents')).toBeVisible()
  expect(screen.queryByText('Calls tools (Pixel and agents)')).toBeNull()
})

test('an initial active lifecycle defers its read and an unchanged idle card does not poll', async () => {
  vi.useFakeTimers()
  const view = render(<ModelProfileSummary modelId="qwen3.5-9b" lifecycleActive/>)
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(fetch).not.toHaveBeenCalled()
  view.rerender(<ModelProfileSummary modelId="qwen3.5-9b" lifecycleActive={false}/>)
  await act(async () => {})
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(300000) })
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('a stalled profile body reaches its read deadline and ignores a late result', async () => {
  vi.useFakeTimers()
  let resolveBody
  fetch.mockResolvedValueOnce({ok: true, json: () => new Promise(resolve => { resolveBody = resolve })})
  const view = render(<ModelProfileSummary modelId="qwen3.5-9b"/>)
  await act(async () => {})
  const signal = fetch.mock.calls[0][1].signal
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveBody(profileBody) })
  expect(view.container).toBeEmptyDOMElement()
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('a late recheck of an unmounted model does not launch another read or change the replacement', async () => {
  await show()
  let resolveRecheck
  fetch.mockImplementationOnce(() => new Promise(resolve => { resolveRecheck = resolve }))
  fireEvent.click(screen.getByRole('button', {name: 'Check again'}))
  // Unmount the old surface while its owner-requested POST is pending.
  const {cleanup} = await import('@testing-library/react')
  cleanup()
  profileBody = {mode: 'observe', modelId: 'new-model', profile: profile({...capable, tools: false}, {modelId: 'new-model'})}
  render(<ModelProfileSummary modelId="new-model"/>)
  await act(async () => {})
  expect(fetch).toHaveBeenCalledTimes(3)
  await act(async () => { resolveRecheck({ok: true, json: async () => ({status: 'recorded'})}) })
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(screen.getByText('No working tool calls: chat only for agents')).toBeVisible()
})
