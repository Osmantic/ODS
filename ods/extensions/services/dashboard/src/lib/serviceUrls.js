export function appendPath(url, path = '') {
  if (!url) return null
  if (!path || path === '/') return url
  return `${url.replace(/\/+$/, '')}/${String(path).replace(/^\/+/, '')}`
}

export function dashboardHost() {
  return typeof window !== 'undefined' ? window.location.hostname : 'localhost'
}

// True when this browser reached the dashboard through the ODS machine's own
// loopback name, so host-loopback-only apps such as OpenCode are reachable.
export function isLoopbackBrowser(hostname = dashboardHost()) {
  const host = String(hostname || '').toLowerCase().replace(/^\[|\]$/g, '')
  return host === 'localhost' || host.endsWith('.localhost') || host === '::1' || /^127(\.\d{1,3}){3}$/.test(host)
}

export function fallbackServiceUrl(port, path = '') {
  return port ? appendPath(`http://${dashboardHost()}:${port}`, path) : null
}

export function serviceUrl(service, path = '') {
  if (!service) return null
  if (service.public_url) return path ? appendPath(service.public_url, path) : service.public_url
  return fallbackServiceUrl(service.external_port ?? service.external_port_default ?? service.port, path || service.ui_path)
}
