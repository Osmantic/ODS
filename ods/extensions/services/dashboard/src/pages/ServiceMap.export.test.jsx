import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ServiceMap from './ServiceMap'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

const payload = { services: [
  { id: 'dashboard', name: 'Dashboard', status: 'healthy', port: 3001, public_url: 'https://private.example/?token=owner-secret' },
  { id: 'dashboard-api', name: 'Dashboard API', status: 'down', port: 3002 },
] }
const readReceipt = () => JSON.parse(decodeURIComponent(screen.getByRole('link', { name: 'Download snapshot' }).href.split(',')[1]))

it.each([false, true])('exports the reported snapshot in compact=%s without requesting new status', async compact => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => payload }))
  render(<ServiceMap compact={compact} />)
  const link = await screen.findByRole('link', { name: 'Download snapshot' })
  const receipt = readReceipt()
  expect(link.download).toBe('ods-integrations.json')
  expect(receipt.schemaVersion).toBe(1)
  expect(Number.isFinite(Date.parse(receipt.capturedAt))).toBe(true)
  expect(receipt.refreshFailed).toBe(false)
  expect(receipt.services).toEqual([
    { id: 'dashboard', name: 'Dashboard', status: 'healthy', port: 3001, category: 'user-facing' },
    { id: 'dashboard-api', name: 'Dashboard API', status: 'down', port: 3002, category: 'middleware' },
  ])
  expect(receipt.knownDependencies).toEqual([{ source: 'dashboard', target: 'dashboard-api', label: 'API', status: 'degraded' }])
  expect(link.href).not.toContain('owner-secret')
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('keeps the full snapshot and its capture time through filters and failed refreshes', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => payload })
    .mockResolvedValueOnce({ ok: false }))
  render(<ServiceMap compact />)
  await screen.findByRole('link', { name: 'Download snapshot' })
  const original = readReceipt()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'no match' } })
  expect(readReceipt()).toEqual(original)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh integrations' }))
  await screen.findByRole('alert')
  expect(readReceipt()).toEqual({ ...original, refreshFailed: true })
})

it('does not offer an empty snapshot as a successful inventory', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ services: [] }) }))
  render(<ServiceMap compact />)
  await screen.findByText('No services reported.')
  expect(screen.queryByRole('link', { name: 'Download snapshot' })).toBeNull()
})
