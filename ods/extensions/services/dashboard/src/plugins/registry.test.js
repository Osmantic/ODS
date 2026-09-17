import { describe, expect, it } from 'vitest'
import { getInternalRoutes, getSidebarExternalLinks } from './registry'

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

  it('keeps the core OpenCode launcher visible when API metadata overrides it', () => {
    const links = getSidebarExternalLinks({
      status: { services: [{ id: 'opencode', name: 'OpenCode (IDE)', status: 'not_deployed' }] },
      getExternalUrl: port => `http://localhost:${port}`,
      apiLinks: [
        {
          id: 'opencode',
          label: 'OpenCode (IDE)',
          port: 3003,
          ui_path: '/',
          icon: 'Code',
          healthNeedles: ['opencode', 'opencode (ide)'],
        },
      ],
    })

    const openCode = links.find(link => link.key === 'opencode')
    expect(openCode).toMatchObject({
      label: 'OpenCode (IDE)',
      url: 'http://localhost:3003',
      healthy: false,
      alwaysVisible: true,
    })
  })
})
