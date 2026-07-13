export function appendPath(url, path = '') {
  if (!url) return null
  if (!path || path === '/') return url
  return `${url.replace(/\/+$/, '')}/${String(path).replace(/^\/+/, '')}`
}

export function dashboardHost() {
  return typeof window !== 'undefined' ? window.location.hostname : 'localhost'
}

export function fallbackServiceUrl(port, path = '') {
  return port ? appendPath(`http://${dashboardHost()}:${port}`, path) : null
}

export function serviceUrl(service, path = '') {
  if (!service) return null
  if (service.public_url) return path ? appendPath(service.public_url, path) : service.public_url
  return fallbackServiceUrl(service.external_port || service.port, path || service.ui_path)
}
