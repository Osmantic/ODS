import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import ServiceMap from './ServiceMap' // eslint-disable-line no-unused-vars
import Extensions from './Extensions' // eslint-disable-line no-unused-vars
import {serviceUrl} from '../lib/serviceUrls'

afterEach(() => {cleanup(); vi.unstubAllGlobals()})

it.each([
  {external_port:0},
  {external_port_default:0},
])('does not fall back from an explicit zero host port: %j', ports => {
  expect(serviceUrl({port:8091, ...ports})).toBeNull()
})

it('retains published, legacy and explicitly configured public routes', () => {
  expect(serviceUrl({port:8091, external_port:9091})).toBe('http://localhost:9091')
  expect(serviceUrl({port:8091})).toBe('http://localhost:8091')
  expect(serviceUrl({port:8091, external_port:0, public_url:'https://egress.example.test/health'})).toBe('https://egress.example.test/health')
})

it.each([false, true])('preserves host-port zero through the service map (public=%s)', async publicRoute => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true, json:async () => ({services:[{
    id:'remote-provider-egress', name:'Remote Provider Egress', status:'healthy',
    port:8091, external_port:0,
    public_url:publicRoute ? 'https://egress.example.test/health' : '',
  }]})}))
  render(<ServiceMap compact />)
  fireEvent.click(await screen.findByRole('button', {name:/Remote Provider Egress/}))
  if (publicRoute) expect(screen.getByRole('link', {name:'Open service'})).toHaveAttribute('href', 'https://egress.example.test/health')
  else expect(screen.queryByRole('link', {name:'Open service'})).toBeNull()
})

it.each([false, true])('honors an internal-only extension with an optional public route (public=%s)', async publicRoute => {
  vi.stubGlobal('fetch', vi.fn(async url => ({ok:true, json:async () => String(url).includes('/api/templates')
    ? {templates:[]} : {agent_available:true, extensions:[{
      id:'remote-provider-egress', name:'Remote Provider Egress', status:'enabled',
      source:'core', port:8091, external_port_default:0, features:[],
      public_url:publicRoute ? 'https://egress.example.test/health' : '',
    }]}})))
  render(<Extensions compact />)
  await screen.findByText('Remote Provider Egress')
  if (publicRoute) expect(screen.getByRole('link')).toHaveAttribute('href', 'https://egress.example.test/health')
  else expect(screen.queryByRole('link')).toBeNull()
})
