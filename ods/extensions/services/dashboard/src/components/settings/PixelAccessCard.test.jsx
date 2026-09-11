import {act, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {afterEach, describe, expect, it, vi} from 'vitest'
import PixelAccessCard from './PixelAccessCard'

const safe = {available: true, surface: 'linux-systemd', configured_mode: 'sandboxed', effective_mode: 'unknown',
  runtime_verified: false, revision: 'a'.repeat(64), busy: false, pending: false, reason: 'runtime-proof-required'}
afterEach(() => vi.unstubAllGlobals())

describe('Pixel access confirmation and effective status', () => {
  it('withdraws effective-mode proof after a failed inspection and requires a fresh receipt', async () => {
    const verified = {...safe, runtime_verified: true, effective_mode: 'sandboxed'}
    const fetch = vi.fn().mockResolvedValueOnce({ok: true, json: async () => verified}).mockResolvedValue({ok: false})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await waitFor(() => expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Safer mode'))
    fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
    await screen.findByRole('alert')
    expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Not verified')
    expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeDisabled()
    fetch.mockResolvedValue({ok: true, json: async () => verified})
    fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
    await waitFor(() => expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeEnabled())
    expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Safer mode')
  })

  it('ignores an older failed read after a newer verified inspection', async () => {
    let finishOld
    const old = new Promise(resolve => {finishOld = resolve})
    const fetch = vi.fn().mockResolvedValueOnce({ok: true, json: async () => safe})
      .mockReturnValueOnce(old).mockResolvedValue({ok: true, json: async () => ({...safe, runtime_verified: true, effective_mode: 'sandboxed'})})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
    fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
    await waitFor(() => expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Safer mode'))
    await act(async () => finishOld({ok: false}))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Safer mode')
  })

  it('keeps a rejected change visible after the recovery inspection succeeds', async () => {
    const fetch = vi.fn(async (_url, options) => options ? {ok: false, status: 409} : {ok: true, json: async () => safe})
    vi.stubGlobal('fetch', fetch)
    render(<PixelAccessCard />)
    await screen.findByText('Not verified')
    fireEvent.click(screen.getByRole('button', {name: 'Verify safer mode'}))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    await waitFor(() => expect(screen.queryByText(/Changing access and checking/)).toBeNull())
    expect(screen.getByRole('alert')).toHaveTextContent('The change was not verified')
    expect(fetch.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1)
  })

  it('ends the inspection message after failure and clears the error on retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ok: false}).mockResolvedValue({ok: true, json: async () => safe}))
    render(<PixelAccessCard />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Assistant access status is unavailable')
    expect(screen.queryByText('Inspecting assistant access…')).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
    await waitFor(() => expect(screen.getByText('Configured')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
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
