import { fireEvent, render, screen } from '@testing-library/react'
import { TemplatePicker } from '../TemplatePicker' // eslint-disable-line no-unused-vars
import { getTemplateStatus } from '../../lib/templates'

const template = { id: 'coding', name: 'Coding', services: ['aider', 'gitea'] }

describe('template cards with installed CLI tools', () => {
  afterEach(() => vi.unstubAllGlobals())

  test('marks an installed CLI and healthy service as applied', () => {
    const extensions = [{ id: 'aider', status: 'cli_installed' }, { id: 'gitea', status: 'enabled' }]
    render(<TemplatePicker templates={[{ ...template, _status: getTemplateStatus(template, extensions) }]} />)
    expect(screen.getByRole('button', { name: /coding/i })).toBeDisabled()
    expect(screen.getByText('Applied')).toBeInTheDocument()
  })

  test.each(['stopped', 'disabled', 'not_installed'])('keeps a %s companion available to apply', status => {
    const extensions = [{ id: 'aider', status: 'cli_installed' }, { id: 'gitea', status }]
    render(<TemplatePicker templates={[{ ...template, _status: getTemplateStatus(template, extensions) }]} />)
    expect(screen.getByRole('button', { name: /coding/i })).toBeEnabled()
  })

  test.each([['error', 'Has errors'], ['installing', /^Installing/]])('preserves %s precedence', (status, label) => {
    const extensions = [{ id: 'aider', status: 'cli_installed' }, { id: 'gitea', status }]
    render(<TemplatePicker templates={[{ ...template, _status: getTemplateStatus(template, extensions) }]} />)
    expect(screen.getByRole('button', { name: /coding/i })).toBeDisabled()
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  test('describes a ready CLI as available rather than a running daemon', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new globalThis.Response(JSON.stringify({
      changes: { to_enable: ['gitea'], already_enabled: ['aider'], incompatible: [] }, warnings: [],
    }))))
    render(<TemplatePicker templates={[template]} />)
    fireEvent.click(screen.getByRole('button', { name: /coding/i }))
    expect(await screen.findByText('Already available')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /apply template/i })).toBeEnabled()
  })
})
