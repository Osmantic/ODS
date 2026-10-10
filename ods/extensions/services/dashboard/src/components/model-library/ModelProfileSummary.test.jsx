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
afterEach(() => vi.unstubAllGlobals())

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
  expect(fetch).toHaveBeenCalledWith('/api/models/qwen3.5-9b/profile', {cache: 'no-store'})
})

test('a chat-only model says so plainly and links to help', async () => {
  profileBody.profile = profile({...capable, tools: false, thinking: {control: 'always', works: true}})
  await show()
  expect(screen.getByText('No working tool calls: chat only for agents')).toBeVisible()
  expect(screen.getByText('Always thinks before it answers')).toBeVisible()
  expect(screen.getByRole('link', {name: 'Get help on Discord'})).toBeVisible()
})

test.each(['always', 'enable_thinking', 'none'])('a failed thinking check does not claim the template capability (%s)', async control => {
  profileBody.profile = profile({...capable, thinking: {control, works: false, separated: false}})
  await show()
  expect(screen.getByText('Thinking check did not pass')).toBeVisible()
  expect(screen.queryByText('Always thinks before it answers')).toBeNull()
  expect(screen.queryByText('Thinking can be turned off')).toBeNull()
  expect(screen.queryByText('Answers without a thinking step')).toBeNull()
  expect(screen.getByText('Answers chat')).toBeVisible()
})

test.each([null, undefined])('an unknown thinking check is not presented as measured (%s)', async works => {
  profileBody.profile = profile({...capable, thinking: {control: 'always', works}})
  await show()
  expect(screen.getByText('Thinking not verified')).toBeVisible()
  expect(screen.queryByText('Always thinks before it answers')).toBeNull()
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
