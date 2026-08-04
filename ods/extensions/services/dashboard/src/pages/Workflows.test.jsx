import { fireEvent, screen, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Workflows from './Workflows' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const catalog = {
  n8nAvailable: true,
  categories: {
    productivity: { name: 'Productivity', description: 'Automate your daily tasks' },
    voice: { name: 'Voice', description: 'Speech-to-text and text-to-speech' },
  },
  workflows: [
    {
      id: 'daily-digest',
      name: 'Daily Digest',
      description: 'Summarize your day every morning',
      icon: 'FileText',
      category: 'productivity',
      status: 'available',
      installed: false,
      active: false,
      dependencies: ['llama-server'],
      dependencyStatus: { 'llama-server': true },
      allDependenciesMet: true,
      setupTime: '2 minutes',
      executions: 0,
    },
    {
      id: 'voice-transcription',
      name: 'Voice Transcription',
      description: 'Turn recordings into text',
      icon: 'Mic',
      category: 'voice',
      status: 'active',
      installed: true,
      active: true,
      dependencies: ['whisper'],
      dependencyStatus: { whisper: true },
      allDependenciesMet: true,
      setupTime: '1 minute',
      executions: 4,
    },
    {
      id: 'code-assistant',
      name: 'Code Assistant',
      description: 'Review diffs on demand',
      icon: 'Code',
      category: 'productivity',
      status: 'available',
      installed: false,
      active: false,
      dependencies: ['openclaw'],
      dependencyStatus: { openclaw: false },
      allDependenciesMet: false,
      setupTime: '3 minutes',
      executions: 0,
    },
  ],
}

const renderWorkflows = (override = null) => {
  const fetchMock = vi.fn(async (url, options) => {
    if (override) {
      const overridden = override(url, options)
      if (overridden) return overridden
    }
    if (url === '/api/workflows') return response(catalog)
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { ...render(<Workflows />), fetchMock }
}

describe('Workflows', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('lists the shipped catalog with its install state', async () => {
    renderWorkflows()

    expect(await screen.findByText('Daily Digest')).toBeInTheDocument()
    expect(screen.getByText('Voice Transcription')).toBeInTheDocument()
    expect(screen.getAllByText('Available')).toHaveLength(2)
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('4 runs')).toBeInTheDocument()
  })

  test('filters the catalog by category', async () => {
    renderWorkflows()
    await screen.findByText('Daily Digest')

    fireEvent.click(screen.getByRole('button', { name: 'Voice' }))

    expect(screen.getByText('Voice Transcription')).toBeInTheDocument()
    expect(screen.queryByText('Daily Digest')).not.toBeInTheDocument()
  })

  test('installing a workflow posts to enable and re-reads the catalog', async () => {
    const { fetchMock } = renderWorkflows((url) => (
      url === '/api/workflows/daily-digest/enable'
        ? response({ status: 'success', message: 'Daily Digest is now active!' })
        : null
    ))
    await screen.findByText('Daily Digest')

    const card = screen.getByText('Daily Digest').closest('div.rounded-lg')
    fireEvent.click(within(card).getByRole('button', { name: /Install/ }))

    expect(await screen.findByText('Daily Digest is now active!')).toBeInTheDocument()
    const enableCall = fetchMock.mock.calls.find(([url]) => url === '/api/workflows/daily-digest/enable')
    expect(enableCall[1].method).toBe('POST')
    // n8n owns install state, so the page must re-read rather than assume.
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/workflows')).toHaveLength(2)
  })

  test('an installed workflow offers removal instead of install', async () => {
    const { fetchMock } = renderWorkflows((url) => (
      url === '/api/workflows/voice-transcription/disable'
        ? response({ status: 'success', message: 'Voice Transcription has been removed' })
        : null
    ))
    await screen.findByText('Voice Transcription')

    const card = screen.getByText('Voice Transcription').closest('div.rounded-lg')
    fireEvent.click(within(card).getByRole('button', { name: /Remove/ }))

    expect(await screen.findByText('Voice Transcription has been removed')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/workflows/voice-transcription/disable')).toBe(true)
  })

  test('blocks install while a dependency is down and names it', async () => {
    renderWorkflows()
    await screen.findByText('Code Assistant')

    expect(screen.getByText('Needs openclaw running first.')).toBeInTheDocument()
    const card = screen.getByText('Code Assistant').closest('div.rounded-lg')
    expect(within(card).getByRole('button', { name: /Install/ })).toBeDisabled()
  })

  test('surfaces the API error instead of silently failing', async () => {
    renderWorkflows((url) => (
      url === '/api/workflows/daily-digest/enable'
        ? response({ detail: 'Missing dependencies: llama-server. Enable these services first.' }, 400)
        : null
    ))
    await screen.findByText('Daily Digest')

    const card = screen.getByText('Daily Digest').closest('div.rounded-lg')
    fireEvent.click(within(card).getByRole('button', { name: /Install/ }))

    expect(await screen.findByText('Missing dependencies: llama-server. Enable these services first.')).toBeInTheDocument()
  })

  test('explains that nothing can be installed while n8n is unreachable', async () => {
    renderWorkflows((url) => (
      url === '/api/workflows'
        ? response({ ...catalog, n8nAvailable: false })
        : null
    ))

    expect(await screen.findByText(/n8n is not reachable/)).toBeInTheDocument()
    const card = screen.getByText('Daily Digest').closest('div.rounded-lg')
    expect(within(card).getByRole('button', { name: /Install/ })).toBeDisabled()
  })

  test('reports a catalog fetch failure', async () => {
    renderWorkflows((url) => (url === '/api/workflows' ? response({}, 503) : null))

    expect(await screen.findByText('Workflow catalog unavailable (503)')).toBeInTheDocument()
  })
})
