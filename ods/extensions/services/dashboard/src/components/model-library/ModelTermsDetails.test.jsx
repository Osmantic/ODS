import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import ModelTermsDetails from './ModelTermsDetails'

const response = {
  modelId: 'model', recordValid: true, releaseReady: false,
  terms: {
    commercial_use: 'not_assessed', upstream_acceptance: 'required_by_observed_gating',
    sources: [{ repository: 'Publisher/Model', revision: 'a'.repeat(40), role: 'artifact_publisher',
      url: 'https://huggingface.co/Publisher/Model/tree/revision',
      declaration_url: 'https://huggingface.co/Publisher/Model/blob/revision/README.md', license_id: 'other', license_name: 'custom-model-terms' }],
    license_documents: [{repo: 'Publisher/Model', url: 'https://publisher.example/license'}],
    artifacts: [{observed_present: true}], note: 'Publisher and base declarations differ.',
  },
}
const reply = (body = response) => ({ok: true, json: async () => body})

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test('loads on demand and displays upstream conditions without accepting or downloading', async () => {
  const fetcher = vi.fn().mockResolvedValue(reply())
  vi.stubGlobal('fetch', fetcher)
  render(<ModelTermsDetails modelId="model" />)
  expect(fetcher).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await screen.findByText('License review pending')
  expect(screen.getByText(/Commercial use: Not yet assessed/)).toBeInTheDocument()
  expect(screen.getByText(/complete it with the upstream publisher/)).toBeInTheDocument()
  expect(screen.getByText('Publisher and base declarations differ.')).toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/models/model/terms')
  expect(screen.queryByRole('button', {name: /download|accept/i})).not.toBeInTheDocument()
})

test('untrusted source URLs remain text and missing artifacts remain visible', async () => {
  const bad = JSON.parse(JSON.stringify(response))
  bad.terms.sources[0].url = 'javascript:alert(1)'
  bad.terms.sources[0].declaration_url = 'https://user:password@publisher.example/'
  bad.terms.license_documents = []
  bad.terms.artifacts[0].observed_present = false
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(bad)))
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await screen.findByText(/configured download file was not found/)
  expect(screen.queryAllByRole('link')).toHaveLength(0)
})

test('read-only details retain commercial restrictions and notice conditions after review', async () => {
  const reviewed = JSON.parse(JSON.stringify(response))
  reviewed.releaseReady = true
  reviewed.terms.commercial_use = 'restricted'
  reviewed.terms.conditions = [{ trigger: 'Redistribution', requirement: 'Preserve the publisher notice.' }]
  reviewed.terms.notice_documents = [{ repository: 'Publisher/Model', path: 'NOTICE', url: 'https://publisher.example/notice' }]
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(reviewed)))
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await screen.findByText('Terms reviewed')
  expect(screen.getByText(/Commercial use: Restricted/)).toBeVisible()
  expect(screen.getByText(/Preserve the publisher notice/)).toBeVisible()
  expect(screen.getByRole('link', {name: 'Publisher/Model: NOTICE'})).toHaveAttribute('href', 'https://publisher.example/notice')
  expect(screen.queryByRole('checkbox')).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('mismatched model response fails visibly and can be retried', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(reply({...response, modelId: 'different'})).mockResolvedValueOnce(reply())
  vi.stubGlobal('fetch', fetcher)
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', {name: 'Retry terms'}))
  await screen.findByText('License review pending')
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('closing the panel aborts its request and late data cannot appear', async () => {
  let finish
  const fetcher = vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetcher)
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => finish(reply()))
  expect(screen.queryByRole('region', {name: 'Model sources and terms'})).not.toBeInTheDocument()
})

test('missing metadata is displayed as incomplete, not as permission', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({modelId: 'model', recordValid: false, terms: null, errors: ['A versioned source and terms record is missing']})))
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await waitFor(() => expect(screen.getByText('Source and license information is incomplete.')).toBeInTheDocument())
  expect(screen.queryByText('Terms reviewed')).not.toBeInTheDocument()
})

test('the deadline includes stalled response-body consumption', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok: true, json: () => new Promise(() => {})}))
  render(<ModelTermsDetails modelId="model" />)
  fireEvent.click(screen.getByRole('button', {name: 'Sources and terms'}))
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.getByRole('alert')).toHaveTextContent('took too long')
  expect(screen.queryByText('Loading sources and terms…')).not.toBeInTheDocument()
})
