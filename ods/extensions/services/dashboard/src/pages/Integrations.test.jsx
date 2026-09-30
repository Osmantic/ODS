import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import Integrations from './Integrations'
import { coreRoutes } from '../plugins/core'
import { getSidebarNavItems } from '../plugins/registry'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const status = {
  servicesCheckedAt: new Date().toISOString(),
  services: [
    { id: 'litellm', name: 'LiteLLM (API Gateway)', status: 'healthy', port: 4000 },
    { id: 'jev-bridge', name: 'Jev bridge', status: 'down', port: 7070 },
  ],
}
const custom = {
  integrations: [{ id: 'jev', name: 'Jev', url: 'https://jev.example.com/health', notes: '', createdAt: status.servicesCheckedAt,
    check: { status: 'healthy', detail: 'Answered HTTP 200', httpStatus: 200, latencyMs: 40, checkedAt: status.servicesCheckedAt } }],
  policy: { maximum: 25, timeoutSeconds: 5 },
}

function stubApi() {
  const fetch = vi.fn(async url => {
    const body = url === '/api/status' ? status : url === '/api/integrations/custom' ? custom : null
    if (!body) throw new Error(`unexpected ${url}`)
    return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

it('is a first-class sidebar page again', () => {
  const route = coreRoutes.find(item => item.id === 'integrations')
  expect(route.path).toBe('/extensions/integrations')
  expect(route.sidebar).toBe(true)
  const labels = getSidebarNavItems({ status: {} }).map(item => item.label)
  expect(labels.indexOf('Integrations')).toBe(labels.indexOf('Extensions') + 1)
})

it('shows your integrations and the ODS services, each with when they were last checked', async () => {
  const fetch = stubApi()
  render(<Integrations />)
  expect(screen.getByRole('heading', { name: 'Integrations', level: 1 })).toBeVisible()

  const yours = await screen.findByRole('listitem', { name: 'Jev: Healthy' })
  expect(yours).toHaveTextContent('Checked just now · HTTP 200 · 40 ms')

  const services = screen.getByRole('region', { name: 'ODS services' })
  expect(await within(services).findByRole('heading', { name: 'ODS services' })).toBeVisible()
  expect(services).toHaveTextContent('2 services · 1 healthy · checked just now')
  fireEvent.click(within(services).getByRole('button', { name: /Jev bridge/ }))
  expect(within(services).getByText('Last check')).toBeVisible()
  expect(within(services).getByRole('button', { name: 'Refresh ODS services' })).toBeVisible()
  expect(fetch.mock.calls.map(([url]) => url).sort()).toEqual(['/api/integrations/custom', '/api/status'])
})

it('drops its own heading inside Settings', async () => {
  stubApi()
  render(<Integrations embedded />)
  await screen.findByRole('listitem', { name: 'Jev: Healthy' })
  expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
  expect(screen.getByRole('heading', { name: 'Your integrations' })).toBeVisible()
})
