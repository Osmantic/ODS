export function appendPath(url, path = '') {
  if (!url) return null
  if (!path || path === '/') return url
  return `${url.replace(/\/+$/, '')}/${String(path).replace(/^\/+/, '')}`
}

export function dashboardHost() {
  return typeof window !== 'undefined' ? window.location.hostname : 'localhost'
}

export function urlHost(hostname) {
  const host = String(hostname || '').trim()
  if (host.includes(':') && !host.startsWith('[')) return `[${host}]`
  return host
}

export function fallbackServiceUrl(port, path = '', hostname = dashboardHost()) {
  return port ? appendPath(`http://${urlHost(hostname)}:${port}`, path) : null
}

export function serviceUrl(service, path = '') {
  if (!service) return null
  if (service.public_url) return path ? appendPath(service.public_url, path) : service.public_url
  return fallbackServiceUrl(service.external_port ?? service.external_port_default ?? service.port, path || service.ui_path)
}
