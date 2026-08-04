import { getInternalRoutes, getSidebarNavItems } from './registry'

// Sidebar predicates receive the same context App passes: { status }.
const withServices = (services) => ({ status: { services } })

describe('core routes', () => {
  test('the workflows route is always registered so direct links resolve', () => {
    const paths = getInternalRoutes(withServices([])).map(route => route.path)
    expect(paths).toContain('/workflows')
  })

  test('workflows stays out of the sidebar when n8n is not part of the stack', () => {
    const ids = getSidebarNavItems(withServices([
      { id: 'open-webui', status: 'healthy' },
    ])).map(item => item.id)
    expect(ids).not.toContain('workflows')
  })

  test('workflows stays out of the sidebar when n8n is not deployed', () => {
    const ids = getSidebarNavItems(withServices([
      { id: 'n8n', status: 'not_deployed' },
    ])).map(item => item.id)
    expect(ids).not.toContain('workflows')
  })

  test('workflows appears once n8n is deployed, even while it is down', () => {
    for (const status of ['healthy', 'unhealthy', 'down']) {
      const ids = getSidebarNavItems(withServices([
        { id: 'n8n', status },
      ])).map(item => item.id)
      expect(ids).toContain('workflows')
    }
  })
})
