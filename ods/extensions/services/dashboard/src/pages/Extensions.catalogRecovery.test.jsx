import { afterEach, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const extension = (status, description = 'Current install') => ({
  id: 'catalog-probe', name: 'Catalog Probe', source: 'core', status,
  description, library_manageable: true, library_selected: true,
  features: [{ category: 'tools', icon: 'Box' }],
})
const catalog = ext => ({ agent_available: true, extensions: [ext], summary: { total: 1, installed: 1 } })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('clears a real catalog 502 banner after a later accepted poll catalog succeeds', async () => {
  let catalogCalls = 0
  const fetchMock = vi.fn(async url => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      catalogCalls += 1
      if (catalogCalls === 1) return reply(catalog(extension('installing')))
      if (catalogCalls === 2) return reply({}, 502)
      return reply(catalog(extension('enabled', 'Recovered catalog')))
    }
    if (target === '/api/extensions/catalog-probe/progress') return reply({ status: 'started' })
    if (target === '/api/webui/selection') return reply({ enabled: false, supported: false })
    if (target === '/api/templates') return reply({ templates: [] })
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<Extensions compact />)
  await screen.findByText('Catalog Probe')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
  expect(await screen.findByText(/Failed to load extensions catalog/)).toBeVisible()
  expect(await screen.findByText('Recovered catalog', {}, { timeout: 8000 })).toBeVisible()
  expect(screen.queryByText(/Failed to load extensions catalog/)).toBeNull()
})

it('keeps a real catalog failure visible when the next poll catalog also fails', async () => {
  let catalogCalls = 0
  const fetchMock = vi.fn(async url => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      catalogCalls += 1
      return catalogCalls === 1 ? reply(catalog(extension('installing'))) : reply({}, 502)
    }
    if (target === '/api/extensions/catalog-probe/progress') return reply({ status: 'started' })
    if (target === '/api/webui/selection') return reply({ enabled: false, supported: false })
    if (target === '/api/templates') return reply({ templates: [] })
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<Extensions compact />)
  await screen.findByText('Catalog Probe')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
  expect(await screen.findByText(/Failed to load extensions catalog/)).toBeVisible()
  await waitFor(() => expect(catalogCalls).toBeGreaterThanOrEqual(3), { timeout: 8000 })
  expect(screen.getByText(/Failed to load extensions catalog/)).toBeVisible()
  expect(screen.getByText('Current install')).toBeVisible()
})

it('does not let an older poll response clear a newer catalog error or replace its catalog', async () => {
  const heldPoll = deferred()
  let catalogCalls = 0
  const fetchMock = vi.fn(async url => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      catalogCalls += 1
      if (catalogCalls === 1) return reply(catalog(extension('installing')))
      if (catalogCalls === 2) return heldPoll.promise
      return reply({}, 502)
    }
    if (target === '/api/extensions/catalog-probe/progress') return reply({ status: 'started' })
    if (target === '/api/webui/selection') return reply({ enabled: false, supported: false })
    if (target === '/api/templates') return reply({ templates: [] })
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<Extensions compact />)
  await screen.findByText('Catalog Probe')
  await waitFor(() => expect(catalogCalls).toBe(2), { timeout: 8000 })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
  expect(await screen.findByText(/Failed to load extensions catalog/)).toBeVisible()
  await act(async () => { heldPoll.resolve(reply(catalog(extension('enabled', 'Stale success')))) })
  expect(screen.getByText(/Failed to load extensions catalog/)).toBeVisible()
  expect(screen.getByText('Current install')).toBeVisible()
  expect(screen.queryByText('Stale success')).toBeNull()
})

it('shows a manual catalog failure when a newer poll also failed', async () => {
  const heldManual = deferred()
  let catalogCalls = 0
  const fetchMock = vi.fn(async url => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      catalogCalls += 1
      if (catalogCalls === 1) return reply(catalog(extension('installing')))
      if (catalogCalls === 2) return heldManual.promise
      return reply({}, 502)
    }
    if (target === '/api/extensions/catalog-probe/progress') return reply({ status: 'started' })
    if (target === '/api/webui/selection') return reply({ enabled: false, supported: false })
    if (target === '/api/templates') return reply({ templates: [] })
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<Extensions compact />)
  await screen.findByText('Catalog Probe')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
  await waitFor(() => expect(catalogCalls).toBeGreaterThanOrEqual(3), { timeout: 8000 })
  await act(async () => { heldManual.resolve(reply({}, 502)) })
  expect(await screen.findByText(/Failed to load extensions catalog/)).toBeVisible()
  expect(screen.getByText('Current install')).toBeVisible()
})
