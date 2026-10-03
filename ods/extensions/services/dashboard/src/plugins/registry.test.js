import { afterEach, describe, expect, it, vi } from 'vitest'
import { getInternalRoutes, getSidebarExternalLinks } from './registry'
import { isLoopbackBrowser } from '../lib/serviceUrls'

vi.mock('../lib/serviceUrls', async importOriginal => ({
  ...(await importOriginal()),
  isLoopbackBrowser: vi.fn(() => true),
}))

describe('getInternalRoutes', () => {
  it('passes the polled system runtime to the Pixel page', () => {
    const status = { inference: { loadedModel: 'Qwen3.5-9B', contextSize: 32768 } }
    const pixel = getInternalRoutes({ status }).find(route => route.id === 'pixel')
    expect(pixel.getProps({ status })).toEqual({ systemStatus: status })
  })
})

describe('getSidebarExternalLinks', () => {
  it('uses API-provided public URLs before host-port fallback', () => {
    const links = getSidebarExternalLinks({
      status: { services: [{ name: 'Open WebUI', status: 'healthy' }] },
      getExternalUrl: port => `http://localhost:${port}`,
      apiLinks: [
        {
          id: 'open-webui',
          label: 'Open WebUI',
          port: 3000,
          ui_path: '/',
          public_url: 'https://chat.example.test',
          healthNeedles: ['Open WebUI'],
        },
      ],
    })

    expect(links.find(link => link.key === 'open-webui').url).toBe('https://chat.example.test')
  })

  it('keeps the existing host-port plus ui_path fallback', () => {
    const links = getSidebarExternalLinks({
      status: { services: [{ name: 'Token Spy', status: 'healthy' }] },
      getExternalUrl: port => `http://localhost:${port}`,
      apiLinks: [
        {
          id: 'token-spy',
          label: 'Token Spy',
          port: 3005,
          ui_path: '/dashboard',
          healthNeedles: ['Token Spy'],
        },
      ],
    })

    expect(links.find(link => link.key === 'token-spy').url).toBe('http://localhost:3005/dashboard')
  })

})

describe('OpenCode application entry', () => {
  const apiLinks = [
    {
      id: 'opencode',
      label: 'OpenCode (IDE)',
      port: 3003,
      ui_path: '/',
      public_url: '',
      icon: 'Code',
      healthNeedles: ['opencode', 'opencode (ide)'],
    },
  ]
  const openCodeWith = (serviceStatus, extra = {}) => getSidebarExternalLinks({
    status: { services: [{ id: 'opencode', name: 'OpenCode (IDE)', status: serviceStatus }] },
    getExternalUrl: port => `http://localhost:${port}`,
    apiLinks: [{ ...apiLinks[0], ...extra }],
  }).find(link => link.key === 'opencode')

  afterEach(() => {
    isLoopbackBrowser.mockReturnValue(true)
  })

  it('is hidden, not permanently offline, when OpenCode was never set up', () => {
    const openCode = openCodeWith('not_deployed')
    expect(openCode).toMatchObject({ state: 'not_installed', visible: false, healthy: false, alwaysVisible: false })
  })

  it('stays hidden while health is still unknown', () => {
    expect(openCodeWith('unknown')).toMatchObject({ state: 'not_installed', visible: false })
  })

  it('leads an installed but stopped OpenCode to its page so it can be started', () => {
    expect(openCodeWith('down')).toMatchObject({
      state: 'stopped', visible: true, healthy: false,
      internalPath: '/apps/opencode', stateLabel: 'Stopped',
    })
  })

  it('shows a starting OpenCode as starting', () => {
    expect(openCodeWith('degraded')).toMatchObject({ state: 'starting', visible: true, stateLabel: 'Starting' })
  })

  it('opens a running OpenCode directly for a browser on the ODS machine', () => {
    expect(openCodeWith('healthy')).toMatchObject({
      state: 'running', healthy: true, url: 'http://localhost:3003', internalPath: null, stateLabel: null,
    })
  })

  it('never links a loopback-only OpenCode by LAN hostname', () => {
    isLoopbackBrowser.mockReturnValue(false)
    expect(openCodeWith('healthy')).toMatchObject({
      state: 'running', healthy: true, url: null, internalPath: '/apps/opencode',
    })
  })

  it('uses an operator-configured public URL from any browser', () => {
    isLoopbackBrowser.mockReturnValue(false)
    expect(openCodeWith('healthy', { public_url: 'https://code.example.test' })).toMatchObject({
      url: 'https://code.example.test', internalPath: null,
    })
  })

  it('registers the OpenCode page outside the primary sidebar', () => {
    const route = getInternalRoutes({}).find(item => item.id === 'opencode-app')
    expect(route).toMatchObject({ path: '/apps/opencode', sidebar: false })
  })
})
