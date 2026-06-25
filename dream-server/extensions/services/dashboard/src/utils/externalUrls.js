const LAN_ROUTE_SUBDOMAINS = new Set(['api', 'auth', 'chat', 'dashboard', 'hermes', 'talk'])

export function formatUrlHost(hostname = 'localhost') {
  const host = String(hostname || 'localhost')
  return host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
}

export function stripLanRouteSubdomain(hostname = 'localhost') {
  const host = String(hostname || 'localhost')
  const parts = host.split('.')
  if (parts.length >= 3 && parts.at(-1) === 'local' && LAN_ROUTE_SUBDOMAINS.has(parts[0])) {
    return parts.slice(1).join('.')
  }
  return host
}

export function buildExternalServiceUrl({ port, path = '', serviceId, hostname } = {}) {
  const browserHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
  const rawHost = hostname || browserHost
  const targetHost = serviceId === 'dream-proxy' ? stripLanRouteSubdomain(rawHost) : rawHost
  const cleanPath = path && path !== '/' ? path : ''
  const portValue = Number(port)
  const omitPort = serviceId === 'dream-proxy' && portValue === 80
  const portPart = portValue && !omitPort ? `:${portValue}` : ''

  return `http://${formatUrlHost(targetHost)}${portPart}${cleanPath}`
}
