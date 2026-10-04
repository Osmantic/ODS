import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * Regression: a manual catalog refresh whose /catalog response is held open
 * must not be able to overwrite a newer catalog snapshot that arrived from a
 * disable mutation. The disable mutation's own fetchCatalog() resolves first
 * (disabled), then the older held-open manual refresh resolves with a stale
 * "stopped" snapshot. The UI must stay disabled.
 *
 * The older response must not replace the completed mutation state.
 *
 * These tests intentionally do NOT mirror the component's internal ordering
 * logic; they only assert the observable UI outcome after the two responses
 * resolve in the order described.
 */

const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })

const cyberchefEnabled = {
  id: 'cyberchef',
  name: 'CyberChef',
  status: 'enabled',
  source: 'user',
  installable: true,
  features: [{ category: 'tools', icon: 'Box' }],
  description: 'Cyber swiss army knife',
}

const cyberchefStopped = { ...cyberchefEnabled, status: 'stopped' }
const cyberchefDisabled = { ...cyberchefEnabled, status: 'disabled' }

const catalogBody = (ext) => ({
  agent_available: true,
  extensions: [ext],
  summary: { total: 1, installed: 1, enabled: ext.status === 'enabled' ? 1 : 0, disabled: ext.status === 'disabled' ? 1 : 0 },
})

// Deferred helper — resolves when we call it.
const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

beforeEach(() => {
  vi.useRealTimers()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Extensions catalog ordering — stale refresh must not resurrect disabled state', () => {
  it('keeps CyberChef disabled when an older held-open manual refresh resolves after the disable mutation', async () => {
    // Sequence of /catalog responses:
    //   1. initial mount  -> enabled
    //   2. manual refresh -> HELD (stale, still says stopped)
    //   3. disable mutation's own fetchCatalog -> disabled
    //   4. held manual refresh resolves -> stale stopped (must be ignored)
    const heldManual = deferred()
    let catalogCalls = 0

    const fetchMock = vi.fn(async (url, options = {}) => {
      const target = String(url)
      if (target === '/api/extensions/catalog') {
        catalogCalls += 1
        if (catalogCalls === 1) return json(catalogBody(cyberchefEnabled))
        if (catalogCalls === 2) return heldManual.promise
        return json(catalogBody(cyberchefDisabled))
      }
      if (target === '/api/webui/selection') return json({ enabled: true, supported: false })
      if (target === '/api/templates') return json({ templates: [] })
      if (target === '/api/extensions/cyberchef/disable' && options.method === 'POST') {
        return json({ message: 'Extension disabled' })
      }
      throw new Error(`Unmocked fetch: ${target}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Extensions compact />)

    // Initial enabled state.
    await screen.findByText('CyberChef')
    expect(screen.getByRole('button', { name: 'Disable CyberChef' })).toBeVisible()

    // Start a manual refresh whose /catalog response is held open.
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    await waitFor(() => expect(catalogCalls).toBe(2))

    // While the manual refresh is pending, disable CyberChef.
    fireEvent.click(screen.getByRole('button', { name: 'Disable CyberChef' }))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm action' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Disable' }))

    // Disable mutation resolves; its own fetchCatalog returns the disabled snapshot.
    await waitFor(() => expect(catalogCalls).toBeGreaterThanOrEqual(3))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enable CyberChef' })).toBeVisible())

    // Now resolve the older held-open manual refresh with a stale stopped snapshot.
    await act(async () => {
      heldManual.resolve(json(catalogBody(cyberchefStopped)))
      await heldManual.promise
    })

    // The UI must remain disabled — the stale response must not overwrite.
    expect(screen.getByRole('button', { name: 'Enable CyberChef' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Disable CyberChef' })).toBeNull()
  })

})
