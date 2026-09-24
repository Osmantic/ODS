import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Dashboard from './Dashboard'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const status = { services: [], gpu: null, inference: {}, uptime: 0 }
const resources = {
  services: [
    { id: 'worker', name: 'Local worker', type: 'docker', restartable: true,
      public_url: 'https://private.example/?key=must-not-export',
      container: { container_name: 'ods-worker', cpu_percent: 125.5, memory_used_mb: 512,
        memory_limit_mb: 2048, memory_percent: 25, pids: 7, environment: 'must-not-export' },
      disk: { data_gb: 2.5, path: 'data/worker' } },
    { id: 'retired-data', name: 'retired-data', type: 'unknown', restartable: false,
      container: null, disk: { data_gb: 1, path: 'data/retired-data' } },
  ],
  totals: { cpu_percent: 125.5, memory_used_mb: 512, disk_data_gb: 3.5 },
  caveats: { docker_desktop_memory: true },
}

function stubResources(responses) {
  let index = 0
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/features') return { ok: true, json: async () => ({ features: [] }) }
    if (url === '/api/services/resources') return responses[Math.min(index++, responses.length - 1)]
    throw new Error(`Unexpected URL ${url}`)
  }))
}

const success = data => ({ ok: true, json: async () => data })
const readExport = () => JSON.parse(decodeURIComponent(screen.getByRole('link', {
  name: 'Export resources',
}).href.split(',')[1]))

it.each([false, true])('exports the complete received resources in compact=%s without another request', async compact => {
  stubResources([success(resources)])
  render(<Dashboard status={status} loading={false} compact={compact} />)
  const link = await screen.findByRole('link', { name: 'Export resources' })
  const receipt = readExport()
  expect(link.download).toBe('ods-resources.json')
  expect(receipt.schemaVersion).toBe(1)
  expect(receipt.source).toBe('/api/services/resources')
  expect(Number.isFinite(Date.parse(receipt.receivedAt))).toBe(true)
  expect(receipt.refreshFailed).toBe(false)
  expect(receipt.services).toHaveLength(2)
  expect(receipt.services[0].container.cpu_percent).toBe(125.5)
  expect(receipt.services[1].container).toBeNull()
  expect(receipt.totals).toEqual(resources.totals)
  expect(receipt.caveats).toEqual(resources.caveats)
  expect(receipt.units).toEqual({ cpu: 'percent (may exceed 100)', memory: 'MB', disk: 'GB' })
  expect(link.href).not.toContain('must-not-export')
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('retains receipt time/data and marks a failed refresh, then replaces them on recovery', async () => {
  const recovered = { ...resources, totals: { ...resources.totals, cpu_percent: 0 } }
  stubResources([success(resources), { ok: false }, success(recovered)])
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  render(<Dashboard status={status} loading={false} />)
  await screen.findByRole('link', { name: 'Export resources' })
  const original = readExport()
  fireEvent(document, new Event('visibilitychange'))
  await screen.findByText('Last refresh failed; export uses the last successful snapshot.')
  expect(readExport()).toEqual({ ...original, refreshFailed: true })
  fireEvent(document, new Event('visibilitychange'))
  await waitFor(() => expect(readExport().refreshFailed).toBe(false))
  expect(readExport().totals.cpu_percent).toBe(0)
  expect(Date.parse(readExport().receivedAt)).toBeGreaterThanOrEqual(Date.parse(original.receivedAt))
})

it.each([{ ok: false }, success({}), success({ services: [] })])('does not offer a missing or empty inventory', async response => {
  stubResources([response])
  render(<Dashboard status={status} loading={false} />)
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  expect(screen.queryByRole('link', { name: 'Export resources' })).toBeNull()
})
