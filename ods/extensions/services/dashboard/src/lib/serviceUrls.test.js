import { describe, expect, it } from 'vitest'
import { appendPath, fallbackServiceUrl, serviceUrl } from './serviceUrls'

describe('service URL helpers', () => {
  it('uses configured public URLs as exact operator-facing links by default', () => {
    expect(serviceUrl({
      public_url: 'https://chat.example.test/proxied-chat',
      ui_path: '/ignored',
      external_port: 3000,
    })).toBe('https://chat.example.test/proxied-chat')
  })

  it('appends explicit launch paths to configured public URLs', () => {
    expect(serviceUrl({
      public_url: 'https://hermes.example.test/base/',
      external_port: 9120,
    }, '/invites')).toBe('https://hermes.example.test/base/invites')
  })

  it('falls back to host-port links with ui paths when no public URL is configured', () => {
    expect(serviceUrl({ external_port: 3005, ui_path: '/dashboard' })).toBe('http://localhost:3005/dashboard')
  })

  it('keeps appendPath and fallback helpers stable for root paths', () => {
    expect(appendPath('https://svc.example.test/', '/')).toBe('https://svc.example.test/')
    expect(fallbackServiceUrl(8080, '/')).toBe('http://localhost:8080')
  })
})
