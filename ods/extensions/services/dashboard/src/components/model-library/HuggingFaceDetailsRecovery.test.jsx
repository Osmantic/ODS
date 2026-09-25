import { Component, createElement } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'

class BrowserBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? createElement('p', null, 'Model browser crashed') : this.props.children }
}
const repo = { id: 'fixture/model', author: 'fixture' }
const details = { id: repo.id, artifacts: [], sha: 'a'.repeat(40) }
const response = body => ({ ok: true, status: 200, json: async () => body })

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

test.each([
  ['HTML response', { ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token <') } }],
  ['error envelope', response({ error: 'Repository metadata unavailable' })],
  ['missing artifact list', response({ ...details, artifacts: null })],
  ['wrong repository', response({ ...details, id: 'another/model' })],
])('keeps malformed %s recoverable in the artifact dialog', async (_name, badResponse) => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(response({ models: [repo] }))
    .mockResolvedValueOnce(badResponse)
    .mockResolvedValueOnce(response(details))
  vi.stubGlobal('fetch', fetch)
  vi.spyOn(console, 'error').mockImplementation(() => {})
  render(createElement(BrowserBoundary, null, createElement(HuggingFaceModelBrowser)))

  await act(async () => { await vi.advanceTimersByTimeAsync(350) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Choose file' })) })
  expect(screen.queryByText('Model browser crashed')).toBeNull()
  expect(screen.getByRole('alert')).toHaveTextContent('Could not read repository metadata. Retry details.')
  expect(screen.queryByRole('button', { name: 'Import', exact: true })).toBeNull()

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry details' })) })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText(/no complete GGUF artifact/i)).toBeVisible()
  expect(fetch.mock.calls.filter(([url]) => url.includes('/repositories/')).map(([url]) => url))
    .toEqual(['/api/models/huggingface/repositories/fixture/model', '/api/models/huggingface/repositories/fixture/model'])
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})
