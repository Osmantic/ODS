// Shared template-status helpers. Kept in a dedicated module (rather than
// re-exported from pages/Extensions.jsx) so components on the initial
// first-run route — e.g. SetupWizard — don't pull the full Extensions page
// into the main bundle and defeat its lazy-loading.

// Services defined in docker-compose.base.yml — always running, not togglable via templates.
export const BASE_COMPOSE_SERVICES = new Set(['llama-server', 'open-webui', 'dashboard', 'dashboard-api'])

// Compute template status from catalog extensions data.
// Returns one of: 'available', 'in_progress', 'applied', 'has_errors'
// Precedence: has_errors > in_progress > applied > available
export function getTemplateStatus(template, extensions) {
  const services = template.services || []
  if (services.length === 0) return 'available'
  const serviceStatus = {}
  for (const svcId of services) {
    if (BASE_COMPOSE_SERVICES.has(svcId)) {
      serviceStatus[svcId] = 'enabled'
      continue
    }
    const ext = extensions.find(e => e.id === svcId)
    serviceStatus[svcId] = ext ? ext.status : undefined
  }
  const statuses = Object.values(serviceStatus)
  if (statuses.some(s => s === 'error')) return 'has_errors'
  if (statuses.some(s => s === 'installing' || s === 'setting_up')) return 'in_progress'
  const allEnabled = statuses.every(s => s === 'enabled')
  if (allEnabled) return 'applied'
  return 'available'
}
