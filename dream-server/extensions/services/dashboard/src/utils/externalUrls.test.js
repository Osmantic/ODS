import { buildExternalServiceUrl, formatUrlHost, stripLanRouteSubdomain } from './externalUrls'

describe('external URL helpers', () => {
  it('keeps direct localhost and IP dream-proxy entry on standard HTTP', () => {
    expect(buildExternalServiceUrl({ serviceId: 'dream-proxy', port: 80, hostname: '127.0.0.1' })).toBe('http://127.0.0.1')
    expect(buildExternalServiceUrl({ serviceId: 'dream-proxy', port: 80, hostname: 'localhost' })).toBe('http://localhost')
    expect(buildExternalServiceUrl({ serviceId: 'dream-proxy', port: 80, hostname: '10.0.0.237' })).toBe('http://10.0.0.237')
  })

  it('collapses dream-proxy service subdomains back to the LAN entry host', () => {
    expect(stripLanRouteSubdomain('dashboard.studio.local')).toBe('studio.local')
    expect(stripLanRouteSubdomain('chat.macbook-air.local')).toBe('macbook-air.local')
    expect(buildExternalServiceUrl({ serviceId: 'dream-proxy', port: 80, hostname: 'dashboard.studio.local' })).toBe('http://studio.local')
  })

  it('preserves non-standard proxy ports and formats IPv6 hosts', () => {
    expect(buildExternalServiceUrl({ serviceId: 'dream-proxy', port: 8080, hostname: 'dashboard.studio.local' })).toBe('http://studio.local:8080')
    expect(buildExternalServiceUrl({ serviceId: 'open-webui', port: 3000, hostname: '::1' })).toBe('http://[::1]:3000')
    expect(formatUrlHost('fe80::1')).toBe('[fe80::1]')
  })
})
