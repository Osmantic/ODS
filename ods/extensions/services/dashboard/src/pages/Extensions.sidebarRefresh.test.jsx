import { fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars
import Sidebar from '../components/Sidebar' // eslint-disable-line no-unused-vars

vi.mock('../plugins/registry', () => ({
  getSidebarNavItems: () => [{ id: 'dashboard', path: '/', label: 'Dashboard', icon: () => <span /> }],
  getSidebarExternalLinks: ({ apiLinks }) => apiLinks.map(link => ({ ...link, icon: () => <span /> })),
}))

const reply = value => ({ ok: true, json: async () => value })

test('Library add refreshes Sidebar links even when catalog status stays the same', async () => {
  let linkEnabled = false
  const catalog = {
    agent_available: true,
    extensions: [{ id: 'whisper', name: 'Whisper', source: 'core', status: 'disabled',
      library_manageable: true, library_selected: false,
      features: [{ category: 'tools', icon: 'Box' }] }],
    summary: { total: 1, installed: 0 },
  }
  const fetchMock = vi.fn(async (url, options = {}) => {
    if (url === '/api/extensions/catalog') return reply(catalog)
    if (url === '/api/templates') return reply({ templates: [] })
    if (url === '/api/webui/selection') return reply({ enabled: false, supported: false })
    if (url === '/api/external-links') return reply(linkEnabled
      ? [{ key: 'voice', label: 'Voice app', url: 'https://voice.example', healthy: true }] : [])
    if (url === '/api/service-tokens') return reply({})
    if (url === '/api/extensions/whisper/enable' && options.method === 'POST') {
      linkEnabled = true
      return reply({ message: 'Selected' })
    }
    if (url === '/api/extensions/whisper/progress') return reply({ status: 'idle' })
    throw new Error(`Unmocked fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<><Sidebar status={{ services: [] }} collapsed={false} onToggle={() => {}} /><Extensions compact /></>)
  fireEvent.click(await screen.findByRole('button', { name: 'Available 1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Turn on Whisper' }))
  fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
  expect(await screen.findByRole('link', { name: 'Voice app' })).toHaveAttribute('href', 'https://voice.example')
})
