import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import HuggingFaceModelBrowser from './HuggingFaceModelBrowser'
import { modelTermsFixture } from '../../test/modelTermsFixture'

const repo = { id: 'publisher/model', author: 'publisher' }
const reply = body => ({ ok: true, json: async () => body })
const artifact = preview => ({ id: 'model-Q4.gguf', label: 'model-Q4.gguf', sizeBytes: 1024, files: [], termsPreview: preview })
const posts = () => fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

async function openTerms() {
  await act(async () => { await vi.advanceTimersByTimeAsync(350) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Choose file' })) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Import', exact: true })) })
  return within(screen.getByRole('dialog', { name: 'Review model terms' }))
}

test('HF preview needs both acknowledgements and never requests terms for an unimported model', async () => {
  const preview = modelTermsFixture('hf-model')
  preview.terms.upstream_acceptance = 'required_by_observed_gating'
  vi.stubGlobal('fetch', vi.fn(async url => reply(url.includes('/search?') ? { models: [repo] }
    : url.endsWith('/import') ? { modelId: 'hf-model', started: true }
      : { ...repo, artifacts: [artifact(preview)] })))
  const onImportStarted = vi.fn()
  render(<HuggingFaceModelBrowser onImportStarted={onImportStarted} />)
  const dialog = await openTerms()
  expect(posts()).toHaveLength(0)
  fireEvent.click(dialog.getByRole('checkbox', { name: /I have reviewed/ }))
  expect(dialog.getByRole('button', { name: 'Confirm import' })).toBeDisabled()
  fireEvent.click(dialog.getByRole('checkbox', { name: /completed the acceptance or access step required by the publisher/ }))
  await act(async () => { fireEvent.click(dialog.getByRole('button', { name: 'Confirm import' })) })
  expect(posts()).toHaveLength(1)
  expect(JSON.parse(posts()[0][1].body)).toEqual({ repoId: repo.id, artifactId: 'model-Q4.gguf',
    termsAcknowledgement: { termsDigest: preview.termsDigest, acknowledged: true, upstreamAccepted: true } })
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/terms'))).toBe(false)
  expect(onImportStarted).toHaveBeenCalledWith({ modelId: 'hf-model', started: true })
})

test.each(['cancel', 'missing', 'invalid'])('HF %s leaves the artifact unimported', async mode => {
  const preview = mode === 'missing' ? undefined : modelTermsFixture('hf-model', mode === 'invalid' ? { recordValid: false, terms: null } : {})
  vi.stubGlobal('fetch', vi.fn(async url => reply(url.includes('/search?') ? { models: [repo] } : { ...repo, artifacts: [artifact(preview)] })))
  const onImportStarted = vi.fn()
  render(<HuggingFaceModelBrowser onImportStarted={onImportStarted} />)
  const dialog = await openTerms()
  expect(dialog.getByRole('button', { name: 'Confirm import' })).toBeDisabled()
  if (mode !== 'cancel') expect(dialog.getByRole('alert')).toBeVisible()
  fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }))
  expect(screen.getByRole('button', { name: 'Import', exact: true })).toBeVisible()
  expect(posts()).toHaveLength(0)
  expect(onImportStarted).not.toHaveBeenCalled()
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/terms'))).toBe(false)
})

test('a changed HF terms response refreshes the preview and requires new acknowledgement', async () => {
  const first = modelTermsFixture('hf-model')
  const updated = { ...first, termsDigest: 'b'.repeat(64) }
  let detailsRequests = 0
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url.includes('/search?')) return reply({ models: [repo] })
    if (url.endsWith('/import')) return { ok: false, status: 409, json: async () => ({ detail: { code: 'model_terms_changed', error: 'Model terms changed. Review again.' } }) }
    return reply({ ...repo, artifacts: [artifact(++detailsRequests === 1 ? first : updated)] })
  }))
  render(<HuggingFaceModelBrowser />)
  const dialog = await openTerms()
  fireEvent.click(dialog.getByRole('checkbox', { name: /I have reviewed/ }))
  await act(async () => { fireEvent.click(dialog.getByRole('button', { name: 'Confirm import' })) })
  expect(posts()).toHaveLength(1)
  expect(detailsRequests).toBe(2)
  expect(screen.getByRole('alert')).toHaveTextContent('Model terms changed. Review again.')
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Import', exact: true })) })
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Confirm import' })).toBeDisabled()
  expect(posts()).toHaveLength(1)
  fireEvent.click(screen.getByRole('checkbox', { name: /I have reviewed/ }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm import' })) })
  expect(JSON.parse(posts()[1][1].body).termsAcknowledgement.termsDigest).toBe(updated.termsDigest)
})
