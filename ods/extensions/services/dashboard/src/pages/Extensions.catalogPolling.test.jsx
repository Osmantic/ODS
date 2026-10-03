import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const catalog = status => ({
  agent_available: true,
  extensions: [{
    id: 'cyberchef', name: 'CyberChef', source: 'user', installable: true,
    status, features: [{ category: 'tools', icon: 'Box' }], description: 'Recipe tools',
  }],
  summary: { total: 1, installed: 1, enabled: status === 'enabled' ? 1 : 0 },
})
const json = data => ({ ok: true, status: 200, json: async () => data })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const resolve = async (request, value) => {
  await act(async () => { request.resolve(value) })
}
const tick = async () => {
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
}
const refresh = () => screen.getByRole('button', { name: 'Refresh extensions' })

describe('Extensions catalog polling response ordering', () => {
  let catalogs
  let progress

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    catalogs = []
    progress = []
    vi.stubGlobal('fetch', vi.fn(url => {
      if (url === '/api/templates') return Promise.resolve(json({ templates: [] }))
      if (url === '/api/webui/selection') return Promise.resolve(json({ enabled: true, supported: false }))
      const requests = url === '/api/extensions/catalog' ? catalogs
        : url === '/api/extensions/cyberchef/progress' ? progress : null
      if (!requests) throw new Error(`Unmocked fetch: ${url}`)
      const request = deferred()
      requests.push(request)
      return request.promise
    }))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  const mountInstalling = async () => {
    render(<Extensions compact />)
    await resolve(catalogs[0], json(catalog('installing')))
    expect(screen.getByText('installing', { exact: true })).toBeVisible()
  }

  it('ignores a poll JSON body arriving after a newer manual refresh', async () => {
    await mountInstalling()
    await tick()
    expect(progress).toHaveLength(1)
    await resolve(progress[0], json({ status: 'idle' }))
    expect(catalogs).toHaveLength(2)
    const body = deferred()
    const readBody = vi.fn(() => body.promise)
    await resolve(catalogs[1], { ok: true, status: 200, json: readBody })
    expect(readBody).toHaveBeenCalledOnce()

    fireEvent.click(refresh())
    expect(catalogs).toHaveLength(3)
    await resolve(catalogs[2], json(catalog('disabled')))
    expect(screen.getByRole('button', { name: 'Enable CyberChef' })).toBeVisible()

    await resolve(body, catalog('enabled'))
    expect(screen.getByRole('button', { name: 'Enable CyberChef' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Disable CyberChef' })).toBeNull()
    expect(screen.queryByText('Extension installed and started.')).toBeNull()
  })

  it('keeps a newer poll result and settles the older manual refresh spinner', async () => {
    await mountInstalling()
    fireEvent.click(refresh())
    expect(catalogs).toHaveLength(2)
    const body = deferred()
    const readBody = vi.fn(() => body.promise)
    await resolve(catalogs[1], { ok: true, status: 200, json: readBody })
    expect(readBody).toHaveBeenCalledOnce()
    expect(refresh()).toBeDisabled()

    await tick()
    await resolve(progress[0], json({ status: 'idle' }))
    expect(catalogs).toHaveLength(3)
    await resolve(catalogs[2], json(catalog('enabled')))
    expect(screen.getByText('enabled', { exact: true })).toBeVisible()

    await resolve(body, catalog('stopped'))
    expect(screen.getByText('enabled', { exact: true })).toBeVisible()
    expect(screen.queryByText('stopped', { exact: true })).toBeNull()
    await waitFor(() => expect(refresh()).not.toBeDisabled())
  })

  it('keeps one progress request in flight and resumes polling after it settles', async () => {
    await mountInstalling()
    await tick()
    expect(progress).toHaveLength(1)
    await tick()
    await tick()
    expect(progress).toHaveLength(1)

    await resolve(progress[0], json({ status: 'idle' }))
    expect(catalogs).toHaveLength(2)
    await resolve(catalogs[1], json(catalog('installing')))
    await tick()
    expect(progress).toHaveLength(2)
    await resolve(progress[1], json({ status: 'downloading', phase_label: 'Downloading' }))
  })

  it('resumes another installation after a mutation invalidates its in-flight poll', async () => {
    const held = deferred()
    let progressCalls = 0
    let otherDisabled = false
    let installed = false
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (url === '/api/templates') return json({ templates: [] })
      if (url === '/api/webui/selection') return json({ enabled: true, supported: false })
      if (url === '/api/extensions/other/disable' && options.method === 'POST') {
        otherDisabled = true
        return json({ message: 'Other service disabled' })
      }
      if (url === '/api/extensions/cyberchef/progress') {
        progressCalls += 1
        if (progressCalls === 1) return held.promise
        installed = true
        return json({ status: 'idle' })
      }
      if (url === '/api/extensions/catalog') {
        const data = catalog(installed ? 'enabled' : 'installing')
        data.extensions.push({ ...data.extensions[0], id: 'other', name: 'Other service', status: otherDisabled ? 'disabled' : 'enabled' })
        return json(data)
      }
      throw new Error(`Unmocked fetch: ${url}`)
    }))
    render(<Extensions compact />)
    await screen.findByText('installing', { exact: true })
    await tick()
    expect(progressCalls).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Disable Other service' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Disable', exact: true })) })
    expect(screen.getByRole('button', { name: 'Enable Other service' })).toBeVisible()

    await resolve(held, json({ status: 'error', error: 'obsolete poll result' }))
    expect(screen.queryByText('obsolete poll result')).toBeNull()
    await tick()
    expect(progressCalls).toBe(2)
    expect(screen.getByRole('button', { name: 'Disable CyberChef' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Enable Other service' })).toBeVisible()
  })
})
