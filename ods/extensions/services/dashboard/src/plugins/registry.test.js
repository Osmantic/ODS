import { describe, expect, it } from 'vitest'
import { getSidebarExternalLinks } from './registry'

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

    expect(links[0].url).toBe('https://chat.example.test')
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

    expect(links[0].url).toBe('http://localhost:3005/dashboard')
  })
})
