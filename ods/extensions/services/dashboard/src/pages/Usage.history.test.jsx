import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Usage from './Usage' // eslint-disable-line no-unused-vars

// The sparkline history store is keyed by period and read back per period, so
// navigating between months must not drop the month you came from.
const STORAGE_KEY = 'ods-usage-summary-history-v1'
const MAY = '2026-05-01:2026-05-31'
const APRIL = '2026-04-01:2026-04-30'

function reportFor(start, end, spend) {
  return {
    period: { start, end },
    source: { name: 'token-spy', status: 'ok', detail: null },
    summary: {
      spend_usd: spend,
      requests: spend * 10,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: spend * 100,
      tracked_providers: 1,
      billing_providers: 1,
      local_providers: 0,
      untracked_providers: 0,
      paid_cost_usd: spend,
      local_cost_usd: 0,
    },
    daily: [],
    models: [],
    services: [],
    sources: [],
  }
}

function installFetchMock() {
  let call = 0
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const text = String(url)
    if (text.includes('/api/usage/readiness')) {
      return { ok: true, json: async () => ({ status: 'ready', actions: {} }) }
    }
    const params = new URLSearchParams(text.split('?')[1] || '')
    call += 1
    return {
      ok: true,
      json: async () => reportFor(params.get('start'), params.get('end'), call),
    }
  }))
}

function storedPeriods() {
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) return []
  return [...new Set(JSON.parse(raw).filter(Boolean).map(item => item.period))].sort()
}

describe('Usage sparkline history retention', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-05-16T12:00:00Z'))
    window.localStorage.setItem(STORAGE_KEY, '[]')
    installFetchMock()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('keeps the current month when the selector moves to the previous one', async () => {
    render(<Usage status={{}} />)
    await waitFor(() => expect(storedPeriods()).toEqual([MAY]))

    fireEvent.click(screen.getByLabelText('Previous month'))

    await waitFor(() => expect(storedPeriods()).toContain(APRIL))
    expect(storedPeriods()).toEqual([APRIL, MAY])
  })

  it('still has the original month after navigating back to it', async () => {
    render(<Usage status={{}} />)
    await waitFor(() => expect(storedPeriods()).toEqual([MAY]))

    fireEvent.click(screen.getByLabelText('Previous month'))
    await waitFor(() => expect(storedPeriods()).toContain(APRIL))
    fireEvent.click(screen.getByLabelText('Next month'))
    await waitFor(() => expect(storedPeriods()).toEqual([APRIL, MAY]))

    const may = JSON.parse(window.localStorage.getItem(STORAGE_KEY))
      .filter(item => item.period === MAY)
    expect(may.length).toBeGreaterThan(1)
  })

  it('ages out samples older than 24h in every period', async () => {
    const stale = Date.parse('2026-05-16T12:00:00Z') - 25 * 60 * 60 * 1000
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([
      { ts: stale, period: APRIL, spend_usd: 9, total_tokens: 0, requests: 0, tracked_providers: 0 },
      { ts: stale, period: MAY, spend_usd: 8, total_tokens: 0, requests: 0, tracked_providers: 0 },
    ]))

    render(<Usage status={{}} />)

    await waitFor(() => expect(storedPeriods()).toEqual([MAY]))
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY))).toHaveLength(1)
  })

  it('renders when storage holds a malformed entry', async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([null, 'nope']))

    render(<Usage status={{}} />)

    await waitFor(() => expect(screen.getByText('Usage')).toBeInTheDocument())
    await waitFor(() => expect(storedPeriods()).toEqual([MAY]))
  })
})
