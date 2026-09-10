import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import {afterEach, describe, expect, it, vi} from 'vitest'
import PixelAccessCard from './PixelAccessCard'

const safe = {available: true, surface: 'linux-systemd', configured_mode: 'sandboxed', effective_mode: 'unknown',
  runtime_verified: false, revision: 'a'.repeat(64), busy: false, pending: false, reason: 'runtime-proof-required'}
afterEach(() => vi.unstubAllGlobals())

describe('Pixel access confirmation and effective status', () => {
  it('does not present configured mode as effective or POST before explicit confirmation', async () => {
    const fetch = vi.fn(async (_url, options) => ({ok: true, json: async () => options ? {...safe, pending: true} : safe}))
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Enable Full Access'}))
    expect(screen.getByRole('button', {name: 'Confirm and enable'})).toBeDisabled()
    expect(fetch.mock.calls.every(call => !call[1])).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', {name: 'Confirm and enable'}))
    await waitFor(() => expect(fetch.mock.calls.some(call => call[1]?.method === 'POST')).toBe(true))
    const options = fetch.mock.calls.find(call => call[1]?.method === 'POST')[1]
    expect(JSON.parse(options.body)).toEqual({mode: 'full-access', confirmed: true, revision: safe.revision})
    expect(screen.queryByText('Effective Full Access')).not.toBeInTheDocument()
  })

  it('blocks changes while work is active and displays a recovery path', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, json: async () => ({...safe, busy: true, pending: true})})))
    render(<PixelAccessCard />)
    await screen.findByText(/transition is unfinished/i)
    expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeDisabled()
    expect(screen.getByRole('button', {name: 'Restore safer mode'})).toBeDisabled()
  })

  it.each(['sandboxed', 'full-access'])('inspects again before requesting %s after Settings becomes stale', async mode => {
    const fresh = {...safe, revision: 'b'.repeat(64)}
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok: true, json: async () => safe})
      .mockResolvedValueOnce({ok: true, json: async () => fresh})
      .mockResolvedValueOnce({ok: true, json: async () => fresh})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    if (mode === 'full-access') {
      fireEvent.click(screen.getByRole('button', {name: 'Enable Full Access'}))
      fireEvent.click(screen.getByRole('checkbox'))
      fireEvent.click(screen.getByRole('button', {name: 'Confirm and enable'}))
    } else fireEvent.click(screen.getByRole('button', {name: 'Verify safer mode'}))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
    expect(fetch.mock.calls[1][1]).toBeUndefined()
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({mode, revision: fresh.revision, confirmed: mode === 'full-access'})
  })

  it.each([
    ['failed inspection', null],
    ['unavailable adapter', {...safe, available: false}],
    ['missing revision', {...safe, revision: undefined}],
    ['newly active work', {...safe, busy: true}],
  ])('does not POST when preflight finds %s', async (_label, fresh) => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok: true, json: async () => safe})
      .mockResolvedValue({ok: Boolean(fresh), json: async () => fresh})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Verify safer mode'}))
    await screen.findByText(/No change was requested/)
    expect(fetch.mock.calls.every(call => !call[1])).toBe(true)
  })

  it('retains safer-mode recovery when fresh inspection finds a pending transition', async () => {
    const fresh = {...safe, pending: true, revision: 'b'.repeat(64)}
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok: true, json: async () => safe})
      .mockResolvedValueOnce({ok: true, json: async () => fresh})
      .mockResolvedValue({ok: true, json: async () => safe})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Verify safer mode'}))
    await waitFor(() => expect(fetch.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1))
    expect(JSON.parse(fetch.mock.calls.find(call => call[1])[1].body).revision).toBe(fresh.revision)
  })

  it('does not enable Full Access when a transition begins after confirmation opens', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok: true, json: async () => safe})
      .mockResolvedValue({ok: true, json: async () => ({...safe, pending: true})})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Enable Full Access'}))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', {name: 'Confirm and enable'}))
    await screen.findByText(/No change was requested/)
    expect(fetch.mock.calls.every(call => !call[1])).toBe(true)
  })

  it('refreshes after a rejected POST without automatically retrying the change', async () => {
    const fetch = vi.fn(async (_url, options) => options
      ? {ok: false, status: 409}
      : {ok: true, json: async () => safe})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Verify safer mode'}))
    await screen.findByText(/The change was not verified/)
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    expect(fetch.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1)
  })
})
