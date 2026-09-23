import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import ModelTermsDialog from './ModelTermsDialog'
import { modelTermsFixture } from '../../test/modelTermsFixture'

const reply = body => ({ ok: true, json: async () => body })
const acknowledge = () => fireEvent.click(screen.getByRole('checkbox', { name: /I have reviewed/ }))
const confirm = () => screen.getByRole('button', { name: 'Confirm download' })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('fetches terms without downloading and sends only the explicitly acknowledged digest', async () => {
  const fetcher = vi.fn().mockResolvedValue(reply(modelTermsFixture('org/model')))
  vi.stubGlobal('fetch', fetcher)
  const onConfirm = vi.fn()
  render(<ModelTermsDialog modelId="org/model" onCancel={vi.fn()} onConfirm={onConfirm} />)
  await screen.findByText('License review pending')
  expect(fetcher).toHaveBeenCalledWith('/api/models/org%2Fmodel/terms', { signal: expect.any(AbortSignal) })
  expect(onConfirm).not.toHaveBeenCalled()
  expect(confirm()).toBeDisabled()
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByText(/Commercial use: Not yet assessed/)).toBeVisible()
  expect(screen.getByText('Declared base models — not reviewed')).toBeVisible()
  expect(screen.getByText('upstream/base')).toBeVisible()
  expect(screen.getByRole('link', { name: "Publisher's declared license link" })).toHaveAttribute('href', 'https://publisher.example/terms')
  expect(screen.getByRole('link', { name: 'publisher/model: NOTICE' })).toHaveAttribute('href', 'https://publisher.example/notice')
  acknowledge()
  fireEvent.click(confirm())
  fireEvent.click(confirm())
  expect(onConfirm).toHaveBeenCalledTimes(1)
  expect(onConfirm).toHaveBeenCalledWith({ termsDigest: 'a'.repeat(64), acknowledged: true })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

test.each([
  ['required', /I have read and accept the applicable terms/, /Read and accept the applicable terms under the recorded conditions/, /completed the acceptance or access step/],
  ['required_by_observed_gating', /completed the acceptance or access step required by the publisher/, /Complete the acceptance or access step required by the publisher/, /I have read and accept the applicable terms/],
])('requires the distinct explicit confirmation for %s', async (upstream_acceptance, checkboxName, explanation, otherKind) => {
  const preview = modelTermsFixture()
  preview.terms.upstream_acceptance = upstream_acceptance
  vi.stubGlobal('fetch', vi.fn())
  const onConfirm = vi.fn()
  render(<ModelTermsDialog modelId="model" preview={preview} onCancel={vi.fn()} onConfirm={onConfirm} />)
  await screen.findByText('License review pending')
  expect(fetch).not.toHaveBeenCalled()
  const upstream = screen.getByRole('checkbox', { name: checkboxName })
  expect(screen.getByText(explanation)).toBeVisible()
  expect(screen.queryByRole('checkbox', { name: otherKind })).toBeNull()
  expect(upstream).not.toBeChecked()
  acknowledge()
  expect(confirm()).toBeDisabled()
  fireEvent.click(upstream)
  expect(confirm()).toBeEnabled()
  fireEvent.click(upstream)
  expect(confirm()).toBeDisabled()
  expect(onConfirm).not.toHaveBeenCalled()
  fireEvent.click(upstream)
  fireEvent.click(confirm())
  expect(onConfirm).toHaveBeenCalledWith({ termsDigest: preview.termsDigest, acknowledged: true, upstreamAccepted: true })
})

test.each([
  ['missing preview', null],
  ['missing terms', modelTermsFixture('model', { terms: null })],
  ['missing digest', modelTermsFixture('model', { termsDigest: undefined })],
  ['wrong model', modelTermsFixture('other')],
  ['invalid record', modelTermsFixture('model', { recordValid: false, terms: null, errors: ['Publisher is missing'] })],
])('blocks confirmation for %s without a fallback request', async (_name, preview) => {
  vi.stubGlobal('fetch', vi.fn())
  const onConfirm = vi.fn()
  render(<ModelTermsDialog modelId="model" preview={preview} onCancel={vi.fn()} onConfirm={onConfirm} />)
  await screen.findByRole('alert')
  expect(confirm()).toBeDisabled()
  expect(screen.queryByRole('checkbox')).toBeNull()
  expect(onConfirm).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

test('network failure remains blocked and Retry terms fetches a fresh record', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('Network unavailable')).mockResolvedValueOnce(reply(modelTermsFixture())))
  render(<ModelTermsDialog modelId="model" onCancel={vi.fn()} onConfirm={vi.fn()} />)
  await screen.findByText('Network unavailable')
  expect(confirm()).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Retry terms' }))
  await screen.findByText('License review pending')
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(fetch).toHaveBeenCalledTimes(2)
})

test('cancelling a pending request aborts it and ignores late success', async () => {
  let finish
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve })))
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  const view = render(<ModelTermsDialog modelId="model" onCancel={onCancel} onConfirm={onConfirm} />)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onCancel).toHaveBeenCalledTimes(1)
  view.unmount()
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => finish(reply(modelTermsFixture())))
  expect(onConfirm).not.toHaveBeenCalled()
})

test.each(['request', 'body'])('enforces a 15 second deadline for a stalled %s', async stage => {
  vi.useFakeTimers()
  const stalled = new Promise(() => {})
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(stage === 'request' ? stalled : Promise.resolve({ ok: true, json: () => stalled })))
  const onConfirm = vi.fn()
  render(<ModelTermsDialog modelId="model" onCancel={vi.fn()} onConfirm={onConfirm} />)
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.getByRole('alert')).toHaveTextContent('took too long')
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  expect(confirm()).toBeDisabled()
  expect(onConfirm).not.toHaveBeenCalled()
})

test('new preview terms clear both checkboxes and bind confirmation to the new digest', async () => {
  const old = modelTermsFixture()
  old.terms.upstream_acceptance = 'required'
  const onConfirm = vi.fn()
  const view = render(<ModelTermsDialog modelId="model" preview={old} onCancel={vi.fn()} onConfirm={onConfirm} />)
  await screen.findByText('License review pending')
  acknowledge()
  fireEvent.click(screen.getByRole('checkbox', { name: /I have read and accept the applicable terms/ }))
  expect(confirm()).toBeEnabled()
  const updated = { ...old, termsDigest: 'c'.repeat(64) }
  view.rerender(<ModelTermsDialog modelId="model" preview={updated} onCancel={vi.fn()} onConfirm={onConfirm} />)
  await screen.findByText('License review pending')
  screen.getAllByRole('checkbox').forEach(input => expect(input).not.toBeChecked())
  expect(confirm()).toBeDisabled()
  acknowledge()
  fireEvent.click(screen.getByRole('checkbox', { name: /I have read and accept the applicable terms/ }))
  fireEvent.click(confirm())
  expect(onConfirm).toHaveBeenCalledWith({ termsDigest: updated.termsDigest, acknowledged: true, upstreamAccepted: true })
})

test('renders unsafe source, license and notice links as text', async () => {
  const preview = modelTermsFixture()
  preview.terms.sources[0].url = 'javascript:alert(1)'
  preview.terms.sources[0].declaration_url = 'https://user:pass@publisher.example/'
  preview.terms.sources[0].license_url = 'data:text/html,bad'
  preview.terms.license_documents[0].url = 'http://publisher.example/'
  preview.terms.notice_documents[0].url = 'javascript:alert(1)'
  render(<ModelTermsDialog modelId="model" preview={preview} onCancel={vi.fn()} onConfirm={vi.fn()} />)
  await screen.findByText('License review pending')
  expect(screen.queryAllByRole('link')).toHaveLength(0)
  expect(screen.getByText('BASE TERMS NOT REVIEWED')).toBeVisible()
})

test('a reviewed restricted model still shows the commercial restriction and recorded conditions', async () => {
  const preview = modelTermsFixture('model', { releaseReady: true })
  preview.terms.commercial_use = 'restricted'
  preview.terms.conditions = [{ trigger: 'Research use', requirement: 'Commercial use requires separate publisher permission.' }]
  preview.terms.local_notices = [{ path: 'licenses/model/NOTICE', source_url: 'https://publisher.example/notice' }]
  render(<ModelTermsDialog modelId="model" preview={preview} onCancel={vi.fn()} onConfirm={vi.fn()} />)
  await screen.findByText('Terms reviewed')
  expect(screen.getByText(/Commercial use: Restricted/)).toBeVisible()
  expect(screen.getByText(/Commercial use requires separate publisher permission/)).toBeVisible()
  expect(screen.getByRole('link', { name: 'licenses/model/NOTICE' })).toHaveAttribute('href', 'https://publisher.example/notice')
  expect(confirm()).toBeDisabled()
})
