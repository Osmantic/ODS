import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import Settings from './Settings' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

const summary = {
  version: '2.6.0',
  install_date: '2026-07-20T14:58:20Z',
  tier: 'entry',
  uptime: 3600,
  services: [
    { id: 'dashboard', name: 'Dashboard', status: 'healthy', port: 3001 },
  ],
}

const storage = {
  models: { formatted: '8.0 GB', gb: 8, percent: 1.6 },
  vector_db: { formatted: '2.0 GB', gb: 2, percent: 0.4 },
  total_data: { formatted: '12.0 GB', gb: 12, percent: 2.4 },
  disk: { used_gb: 62.5, total_gb: 500, percent: 12.5 },
}

const editor = {
  path: '.env',
  fields: {
    ODS_VERSION: {
      key: 'ODS_VERSION',
      label: 'ODS Version',
      description: 'ODS version for update compatibility checks.',
      type: 'string',
      secret: false,
      required: false,
      readOnly: true,
      default: null,
    },
    HOST_LAN_IP: {
      key: 'HOST_LAN_IP',
      label: 'LAN Host IP',
      description: 'Host address exposed to services.',
      type: 'string',
      secret: false,
      required: false,
      readOnly: false,
      default: null,
    },
  },
  sections: [{ id: 'configuration', title: 'Configuration', keys: ['ODS_VERSION', 'HOST_LAN_IP'] }],
  values: { ODS_VERSION: '2.6.0', HOST_LAN_IP: '192.168.1.10' },
  issues: [],
  applyPlan: null,
  agentAvailable: true,
}

const payloadByUrl = (url) => {
  if (url === '/api/pixel/providers') return { configuration: {
    schemaVersion: 1, revision: 0, enabled: false, providers: [],
    roles: { leader: null, backups: [], advisor: null, handoff: null },
    policy: { allowCloud: false, maxAttempts: 3, deadlineSeconds: 120 },
  }, runtime: { status: 'not-applied' } }
  if (url === '/api/settings/summary') return summary
  if (url === '/api/storage') return storage
  if (url === '/api/settings/env') return editor
  if (String(url).startsWith('/api/usage/report?')) {
    return {
      summary: { total_tokens: 16400, requests: 42 },
      models: [{ model: 'qwen' }, { model: 'phi' }],
      source: { status: 'ok' },
    }
  }
  if (url === '/api/setup/status') return { first_run: false, persona: null }
  if (url === '/api/version' || url === '/api/version?force=true') {
    return { current: '2.6.0', latest: '2.6.0', update_available: false, checked_at: '2026-07-23T12:00:00Z' }
  }
  throw new Error(`Unexpected request: ${url}`)
}

const renderSettings = (override = null) => {
  const fetchMock = vi.fn(async (url) => {
    if (override) {
      const overridden = override(url)
      if (overridden) return overridden
    }
    return response(payloadByUrl(url))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { ...render(<Settings />), fetchMock }
}

describe('Settings', () => {
  it('inspects permissions when its section opens, without polling a hidden mounted card', async () => {
    const access = {available:true,surface:'wsl-systemd',configured_mode:'full-access',
      effective_mode:'full-access',runtime_verified:true,revision:'a'.repeat(64),pending:false,busy:false}
    const fetchMock = vi.fn(async url => response(url === '/api/pixel/access-mode' ? access : payloadByUrl(url)))
    vi.stubGlobal('fetch', fetchMock)
    const view = render(<Settings activeSection="general" />)
    await act(async () => {})
    const inspections = () => fetchMock.mock.calls.filter(([url]) => url === '/api/pixel/access-mode')
    expect(inspections()).toHaveLength(0)
    view.rerender(<Settings activeSection="access" />)
    expect(await screen.findByText('WSL')).toBeVisible()
    expect(inspections()).toHaveLength(1)
    view.rerender(<Settings activeSection="general" />)
    fireEvent.focus(window)
    await act(async () => {})
    expect(inspections()).toHaveLength(1)
    view.rerender(<Settings activeSection="access" />)
    await waitFor(() => expect(inspections()).toHaveLength(2))
    expect(fetchMock.mock.calls.every(call => call[1]?.method !== 'POST')).toBe(true)
  })

  it('shows a confirmed release with notes and performs only a read when checking', async () => {
    const { fetchMock } = renderSettings(url => String(url).startsWith('/api/version') ? response({ current:'2.6.0', latest:'2.7.0', update_available:true, check_status:'checked', checked_at:'2026-09-16T00:00:00Z' }) : null)
    expect(await screen.findByText('A new version of ODS is available')).toBeVisible()
    expect(screen.getByRole('link', {name:'Release notes'})).toHaveAttribute('href','https://github.com/Osmantic/ODS/releases/tag/v2.7.0')
    fireEvent.click(screen.getByRole('button', {name:'Check', exact:true}))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/version?force=true')).toBe(true))
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/update')).toBe(false)
  })

  it('never labels a failed release check as up to date', async () => {
    renderSettings(url => String(url).startsWith('/api/version') ? response({ current:'2.6.0', latest:null, update_available:false, check_status:'unavailable', checked_at:null }) : null)
    expect(await screen.findByText('Update check unavailable')).toBeVisible()
    fireEvent.click(screen.getByRole('button', {name:'Check', exact:true}))
    expect(await screen.findByText('Could not confirm the latest release. Try checking again.')).toBeVisible()
    expect(screen.queryByText('Current release')).toBeNull()
  })

  it('says why a held release check has no answer instead of asking to try again', async () => {
    // Workstation06 (2026-10-27): the site's address had used up GitHub's unauthenticated requests.
    const reason = "GitHub's request limit for this network is used up; try again in 12 min"
    renderSettings(url => String(url).startsWith('/api/version') ? response({ current:'3.0.0', latest:null, update_available:false, check_status:'unavailable', checked_at:null, check_error:reason }) : null)
    expect(await screen.findByText('Update check unavailable')).toBeVisible()
    fireEvent.click(screen.getByRole('button', {name:'Check', exact:true}))
    expect(await screen.findByText(`${reason}.`)).toBeVisible()
    expect(screen.queryByText('Could not confirm the latest release. Try checking again.')).toBeNull()
  })
  it('does not invent uptime or a live state when uptime is missing', async () => {
    renderSettings(url => url === '/api/settings/summary' ? response({...summary,uptime:undefined}) : null)
    const label = await screen.findByText('Uptime')
    const row = label.closest('.settings-meta-row')
    expect(within(row).getByText('Unknown')).toBeVisible()
    expect(within(row).queryByText('Live')).toBeNull()
  })
  it('preserves unsaved provider edits during a system refresh', async () => {
    const { fetchMock } = renderSettings()
    await screen.findByText('No providers configured.', {}, { timeout: 3000 })
    fireEvent.change(screen.getByLabelText('New provider ID'), { target: { value: 'unsaved' } })
    fireEvent.change(screen.getByLabelText('New provider label'), { target: { value: 'Unsaved provider' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add provider', exact: true }))
    fireEvent.change(screen.getByLabelText('Model', { exact: true }), { target: { value: 'keep-this-model' } })
    const header = screen.getByRole('heading', { name: 'Settings', exact: true }).closest('header')
    fireEvent.click(within(header).getByRole('button', { name: 'Refresh', exact: true }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings/summary')).toHaveLength(2))
    expect(screen.getByLabelText('Model', { exact: true })).toHaveValue('keep-this-model')
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/pixel/providers')).toHaveLength(1)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-theme')
  })

  it('re-checks the ODS session before a route row opens Hermes', async () => {
    const tab = { opener: window, closed: false, location: { replace: vi.fn() } }
    vi.spyOn(window, 'open').mockReturnValue(tab)
    const { fetchMock } = renderSettings(url => {
      if (url === '/api/settings/summary') {
        return response({ ...summary, services: [{ id: 'hermes-proxy', name: 'Hermes Auth Proxy', status: 'healthy', port: 9120, external_port: 9120 }] })
      }
      if (url === '/api/auth/verify-session') return response({ detail: 'Invalid or expired session' }, 401)
      if (url === '/api/auth/admin-session') return response({ ok: true })
      return null
    })
    const row = await screen.findByRole('link', { name: /Hermes Auth Proxy/ })
    expect(fireEvent.click(row)).toBe(false)
    expect(window.open).toHaveBeenCalledWith('', '_blank')
    await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith('http://localhost:9120/'))
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/admin-session', expect.objectContaining({ method: 'POST' }))
  })

  test('renders storage capacity and an ODS data breakdown from the API contract', async () => {
    renderSettings()

    expect(await screen.findByRole('heading', { name: 'Storage' })).toBeInTheDocument()
    expect(screen.getByText('12.0 GB')).toBeInTheDocument()
    expect(screen.getByText('62.5 GB of 500.0 GB')).toBeInTheDocument()
    expect(screen.getByText('./data')).toBeInTheDocument()
    expect(screen.getByText('Models')).toBeInTheDocument()
    expect(screen.getByText('Vector DB')).toBeInTheDocument()
    expect(screen.getByText('Service data')).toBeInTheDocument()
    expect(screen.queryByText('Other ODS data')).not.toBeInTheDocument()
    expect(screen.getByText('Includes bind-mounted ODS service data. Docker image layers are managed separately by Docker.')).toBeInTheDocument()
    expect(screen.getByLabelText('Host disk 12.5% used')).toBeInTheDocument()
    const accountCard = screen.getByRole('link', { name: /Account Usage/ })
    expect(within(accountCard).getByText('16.4k')).toBeInTheDocument()
    expect(within(accountCard).getByText('42')).toBeInTheDocument()
    expect(within(accountCard).getByText('2')).toBeInTheDocument()
  })

  test('clamps malformed storage values without rendering invalid widths', async () => {
    const { container } = renderSettings((url) => (
      url === '/api/storage'
        ? response({
            models: { gb: -3 },
            vector_db: { gb: 'invalid' },
            total_data: { gb: 4 },
            disk: { used_gb: -8, total_gb: 0, percent: 250 },
          })
        : null
    ))

    expect(await screen.findByText('Disk capacity unavailable')).toBeInTheDocument()
    expect(screen.getByLabelText('Host disk 100% used').firstElementChild).toHaveStyle({ width: '100%' })
    expect(container.innerHTML).not.toContain('NaN')
    expect(container.innerHTML).not.toContain('Infinity')
  })

  test('counts unique model identities instead of per-service report rows', async () => {
    renderSettings((url) => (
      String(url).startsWith('/api/usage/report?')
        ? response({
            summary: { total_tokens: 100, requests: 3 },
            models: [
              { model: 'qwen', service: 'litellm' },
              { model: 'qwen', service: 'model-router' },
              { model: 'phi', service: 'hermes' },
            ],
            source: { status: 'ok' },
          })
        : null
    ))

    const accountCard = await screen.findByRole('link', { name: /Account Usage/ })
    const modelMetric = within(accountCard).getByText('Models Used').parentElement
    expect(within(modelMetric).getByText('2')).toBeInTheDocument()
  })

  test('keeps successfully loaded settings visible when storage is unavailable', async () => {
    renderSettings((url) => (
      url === '/api/storage'
        ? response({ detail: 'storage probe failed' }, 503)
        : null
    ))

    expect(await screen.findByRole('heading', { name: 'System Identity' })).toBeInTheDocument()
    expect(screen.getByText('Some settings details are temporarily unavailable. Showing the data that loaded successfully.')).toBeInTheDocument()
    expect(screen.getByText('Disk capacity unavailable')).toBeInTheDocument()
    expect(screen.getByText('No data yet')).toBeInTheDocument()
    expect(screen.getAllByText('Empty')).toHaveLength(3)
  })

  test('migrates a retired theme to the shared Portal appearance', async () => {
    localStorage.setItem('ods-theme', 'light')
    const { container } = renderSettings()
    await screen.findByRole('heading', { name: 'System Identity' })

    expect(screen.queryByRole('button', { name: 'Light' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Portal', exact: true })).toHaveAttribute('aria-pressed', 'true')

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'ods'))
    expect(localStorage.getItem('ods-theme')).toBe('ods')
    const cards = [...container.querySelectorAll('.settings-section')]
    expect(cards.length).toBeGreaterThan(0)
    expect(container.querySelector('.liquid-metal-frame')).not.toBeInTheDocument()
    expect(cards.every(card => !card.getAttribute('style')?.includes('rgba(18,18,25'))).toBe(true)
  })

  test('does not render controls that advertise unimplemented field help or search shortcuts', async () => {
    renderSettings()

    expect(await screen.findByRole('heading', { name: 'Environment Editor' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'More information about ODS Version' })).not.toBeInTheDocument()
    expect(screen.queryByText('K', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'localhost' })).not.toBeInTheDocument()
  })

  test('keeps compact route filters functional and allows collapsing the expanded list', async () => {
    renderSettings(url => url === '/api/settings/summary' ? response({ ...summary, services: Array.from({ length: 6 }, (_, index) => ({ id: `route-${index}`, name: `Test route ${index}`, status: 'healthy', port: 8000 + index })) }) : null)
    const filters = await screen.findByRole('group', { name: 'Filter routes' })
    expect(within(filters).getByRole('button', { name: 'all', exact: true })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '2 more routes' }))
    expect(screen.getByText('Test route 5')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show fewer routes' }))
    expect(screen.queryByText('Test route 5')).not.toBeInTheDocument()
    fireEvent.click(within(filters).getByRole('button', { name: 'inactive', exact: true }))
    expect(screen.getByText('No routes match this filter.')).toBeInTheDocument()
    expect(within(filters).getByRole('button', { name: 'inactive', exact: true })).toHaveAttribute('aria-pressed', 'true')
  })

  test('includes undeployed and stopped services in the inactive filter', async () => {
    renderSettings(url => url === '/api/settings/summary' ? response({...summary,
      services: [
        {id:'absent',name:'Undeployed service',status:'not_deployed',port:8001},
        {id:'stopped',name:'Stopped service',status:'stopped',port:8002},
        {id:'ready',name:'Ready service',status:'healthy',port:8003},
      ],
    }) : null)
    const filters = await screen.findByRole('group', {name:'Filter routes'})
    fireEvent.click(within(filters).getByRole('button', {name:'inactive',exact:true}))
    expect(screen.getByText('Undeployed service')).toBeInTheDocument()
    expect(screen.getByText('Stopped service')).toBeInTheDocument()
    expect(screen.queryByText('Ready service')).not.toBeInTheDocument()
  })

  test('preserves unsaved environment changes during a global refresh', async () => {
    const { fetchMock } = renderSettings()
    const input = await screen.findByDisplayValue('192.168.1.10')
    fireEvent.change(input, { target: { value: '192.168.1.25' } })

    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])

    await screen.findByText('System details refreshed. Unsaved environment changes were preserved.')
    expect(screen.getByDisplayValue('192.168.1.25')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings/env')).toHaveLength(2)
  })

  test('preserves unsaved environment changes during editor refresh and reloads them only on explicit reload', async () => {
    const { fetchMock } = renderSettings()
    const input = await screen.findByDisplayValue('192.168.1.10')
    const environmentEditor = screen.getByRole('heading', { name: 'Environment Editor' }).closest('section')
    fireEvent.change(input, { target: { value: '192.168.1.25' } })

    fireEvent.click(within(environmentEditor).getByRole('button', { name: 'Refresh' }))

    await screen.findByText('System details refreshed. Unsaved environment changes were preserved.')
    expect(screen.getByDisplayValue('192.168.1.25')).toBeInTheDocument()

    const refreshedEnvironmentEditor = screen.getByRole('heading', { name: 'Environment Editor' }).closest('section')
    fireEvent.click(within(refreshedEnvironmentEditor).getByRole('button', { name: 'Reload' }))

    await screen.findByText('Environment editor reloaded from disk.')
    expect(screen.getByDisplayValue('192.168.1.10')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings/env')).toHaveLength(3)
  })

  test('preserves unsaved environment changes when retrying a failed refresh', async () => {
    let offline = false
    renderSettings((url) => (
      offline && ['/api/settings/summary', '/api/storage', '/api/settings/env'].includes(url)
        ? response({ detail: 'dashboard-api restarting' }, 503)
        : null
    ))
    const input = await screen.findByDisplayValue('192.168.1.10')
    fireEvent.change(input, { target: { value: '192.168.1.25' } })

    offline = true
    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])
    const retry = await screen.findByRole('button', { name: 'Retry' })
    expect(screen.getByDisplayValue('192.168.1.25')).toBeInTheDocument()

    offline = false
    fireEvent.click(retry)

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument())
    expect(screen.getByDisplayValue('192.168.1.25')).toBeInTheDocument()
    expect(screen.getByText('System details refreshed. Unsaved environment changes were preserved.')).toBeInTheDocument()
  })
})

it.each([true,false])('locks the environment draft until its pending save settles (success=%s)', async success => {
  const {fetchMock} = renderSettings()
  const field = await screen.findByLabelText('LAN Host IP')
  fireEvent.change(field,{target:{value:'192.168.1.25'}})
  let finish
  const original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url,options) => options?.method === 'PUT'
    ? new Promise(resolve => {finish = resolve})
    : original(url,options))
  fireEvent.click(screen.getByRole('button',{name:'Save .env'}))
  expect(field).toBeDisabled()
  const env = screen.getByRole('heading',{name:'Environment Editor'}).closest('section')
  expect(within(env).getByRole('button',{name:'Reload'})).toBeDisabled()
  expect(within(env).getByRole('button',{name:'Refresh',exact:true})).toBeDisabled()
  await act(async () => finish(response(success
    ? {...editor,values:{...editor.values,HOST_LAN_IP:'192.168.1.25'}}
    : {detail:'Write failed'}, success ? 200 : 503)))
  expect(field).toBeEnabled()
  expect(field).toHaveValue('192.168.1.25')
  fireEvent.change(field,{target:{value:'192.168.1.26'}})
  expect(screen.getByRole('button',{name:'Save .env'})).toBeEnabled()
  expect(fetchMock.mock.calls.filter(([,options]) => options?.method === 'PUT')).toHaveLength(1)
})

it.each([true, false])('protects saved settings while runtime apply is pending and recovers (success=%s)', async success => {
  const plan = {status:'pending',supported:true,envRevision:'a'.repeat(64),services:['litellm'],summary:'Restart LiteLLM.'}
  const {fetchMock} = renderSettings(url => url === '/api/settings/env'
    ? response({...editor,applyPlan:plan}) : null)
  const field = await screen.findByLabelText('LAN Host IP')
  let finish
  const original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url, options) => url === '/api/settings/env/apply'
    ? new Promise(resolve => {finish = resolve})
    : options?.method === 'PUT'
      ? response({...editor,values:JSON.parse(options.body).values,applyPlan:plan})
      : original(url, options))

  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(field).toBeDisabled()
  const env = screen.getByRole('heading',{name:'Environment Editor'}).closest('section')
  expect(within(env).getByRole('button',{name:'Reload'})).toBeDisabled()
  screen.getAllByRole('button',{name:'Refresh',exact:true}).forEach(button => expect(button).toBeDisabled())
  expect(screen.getByRole('button',{name:'Applying...'})).toBeDisabled()
  const navigation = new Event('beforeunload',{cancelable:true})
  window.dispatchEvent(navigation)
  expect(navigation.defaultPrevented).toBe(true)
  const envReads = () => fetchMock.mock.calls.filter(([url, options]) => url === '/api/settings/env' && !options?.method)
  expect(envReads()).toHaveLength(1)

  await act(async () => finish(response(success ? {message:'Runtime applied.'}
    : {detail:'Host restart failed'}, success ? 200 : 503)))
  expect(field).toBeEnabled()
  expect(field).toHaveValue('192.168.1.10')
  screen.getAllByRole('button',{name:'Refresh',exact:true}).forEach(button => expect(button).toBeEnabled())
  if (!success) {
    expect(screen.getByText('Host restart failed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
    await act(async () => finish(response({message:'Runtime applied.'})))
  }
  expect(screen.getByText('Runtime applied.')).toBeInTheDocument()
  const settledNavigation = new Event('beforeunload',{cancelable:true})
  window.dispatchEvent(settledNavigation)
  expect(settledNavigation.defaultPrevented).toBe(false)

  // A later save gets its own pending plan rather than being settled by the
  // earlier apply response. Mutating buttons are available again.
  fireEvent.change(field,{target:{value:'192.168.1.25'}})
  fireEvent.click(screen.getByRole('button',{name:'Save .env'}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled())
  expect(field).toHaveValue('192.168.1.25')
  expect(fetchMock.mock.calls.filter(([,options]) => options?.method === 'PUT')).toHaveLength(1)
})

it('keeps saved changes and retries a busy Apply without another Save', async () => {
  const plan = {status:'pending',supported:true,envRevision:'a'.repeat(64),services:['llama-server'],summary:'Restart llama-server.'}
  const busy = 'ODS is finishing another operation. Your settings are saved. Wait for it to finish, then choose Apply changes again.'
  const {fetchMock} = renderSettings()
  const field = await screen.findByLabelText('LAN Host IP')
  const original = fetchMock.getMockImplementation()
  let applyCalls = 0
  fetchMock.mockImplementation((url, options) => {
    if (url === '/api/settings/env' && options?.method === 'PUT') {
      return response({...editor,values:JSON.parse(options.body).values,applyPlan:plan})
    }
    if (url === '/api/settings/env/apply') {
      applyCalls += 1
      return applyCalls === 1
        ? response({detail:{code:'model_lifecycle_busy',message:busy}},409)
        : response({message:'Runtime applied.'})
    }
    return original(url, options)
  })

  fireEvent.change(field,{target:{value:'192.168.1.25'}})
  fireEvent.click(screen.getByRole('button',{name:'Save .env'}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(await screen.findByText(busy)).toBeInTheDocument()
  expect(field).toHaveValue('192.168.1.25')
  expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled()
  expect(fetchMock.mock.calls.filter(([,options]) => options?.method === 'PUT')).toHaveLength(1)

  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(await screen.findByText('Runtime applied.')).toBeInTheDocument()
  expect(screen.getByRole('button',{name:'Apply changes'})).toBeDisabled()
  expect(applyCalls).toBe(2)
  expect(fetchMock.mock.calls.filter(([,options]) => options?.method === 'PUT')).toHaveLength(1)
  const requests = fetchMock.mock.calls.filter(([url]) => url === '/api/settings/env/apply')
  expect(requests.map(([,options]) => JSON.parse(options.body)))
    .toEqual([{service_ids:['llama-server'],expected_env_revision:'a'.repeat(64)},{service_ids:['llama-server'],expected_env_revision:'a'.repeat(64)}])
})

it.each([['Refresh',true],['Refresh',false],['Reload',true],['Reload',false]])('does not start runtime apply before an environment %s settles (success=%s)', async (action, success) => {
  const plan = {status:'pending',supported:true,envRevision:'a'.repeat(64),services:['litellm'],summary:'Restart LiteLLM.'}
  const {fetchMock} = renderSettings(url => url === '/api/settings/env'
    ? response({...editor,applyPlan:plan}) : null)
  const field = await screen.findByLabelText('LAN Host IP')
  let finish
  const original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url, options) => url === '/api/settings/env'
    ? new Promise(resolve => {finish = resolve}) : original(url, options))
  const env = screen.getByRole('heading',{name:'Environment Editor'}).closest('section')
  fireEvent.click(within(env).getByRole('button',{name:action,exact:true}))
  expect(field).toBeDisabled()
  expect(screen.getByRole('button',{name:'Apply changes'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/settings/env/apply')).toBe(false)
  await act(async () => finish(response(success ? {...editor,applyPlan:plan}
    : {detail:'Environment read failed'}, success ? 200 : 503)))
  expect(field).toBeEnabled()
  expect(field).toHaveValue('192.168.1.10')
  expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled()
  if (!success && action === 'Reload') expect(screen.getByText('Environment read failed')).toBeInTheDocument()
})


it('requires a saved configuration revision before sending Apply', async () => {
  const plan = {status:'pending',supported:true,services:['llama-server']}
  const {fetchMock} = renderSettings(url => url === '/api/settings/env' ? response({...editor,applyPlan:plan}) : null)
  await screen.findByLabelText('LAN Host IP')
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(await screen.findByText('Reload the current settings, then review and save your changes before applying.')).toBeVisible()
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/settings/env/apply')).toBe(false)
})

it.each([true,false])('rejects a stale saved plan and refreshes current settings (refresh succeeds=%s)', async refreshSucceeds => {
  const modelEditor = {...editor,
    fields:{LLAMA_PARALLEL:{key:'LLAMA_PARALLEL',label:'Llama Parallel',type:'integer',secret:false,required:false,readOnly:false}},
    sections:[{id:'configuration',title:'Configuration',keys:['LLAMA_PARALLEL']}],
    values:{LLAMA_PARALLEL:'1'}}
  const plan = {status:'ready',supported:true,services:['llama-server'],envRevision:'a'.repeat(64),summary:'Restart llama-server.'}
  let envReads = 0
  let saves = 0
  let applies = 0
  const fetchMock = vi.fn(async (url,options) => {
    if (url === '/api/settings/env') {
      if (options?.method === 'PUT') {
        saves += 1
        return response({...modelEditor,values:JSON.parse(options.body).values,applyPlan:{...plan,envRevision:(saves === 1 ? 'a' : 'b').repeat(64)}})
      }
      envReads += 1
      return envReads > 1 && !refreshSucceeds ? response({detail:'Settings temporarily unavailable'},503) : response(modelEditor)
    }
    if (url === '/api/settings/env/apply') {
      applies += 1
      return applies === 1 ? response({detail:{code:'env_revision_mismatch',message:'Settings changed'}},409) : response({message:'Runtime applied.'})
    }
    return response(payloadByUrl(url))
  })
  vi.stubGlobal('fetch',fetchMock)
  render(<Settings />)
  let field = await screen.findByLabelText('Llama Parallel')
  fireEvent.change(field,{target:{value:'2'}})
  fireEvent.click(screen.getByRole('button',{name:'Save .env'}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  await screen.findByText(/Settings changed while you were waiting/)
  expect(screen.getByRole('button',{name:'Apply changes'})).toBeDisabled()
  expect(saves).toBe(1)
  expect(applies).toBe(1)
  expect(envReads).toBe(2)
  if (!refreshSucceeds) return
  field = screen.getByLabelText('Llama Parallel')
  expect(field).toHaveValue(1)
  fireEvent.change(field,{target:{value:'2'}})
  fireEvent.click(screen.getByRole('button',{name:'Save .env'}))
  await waitFor(() => expect(screen.getByRole('button',{name:'Apply changes'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'Apply changes'}))
  expect(await screen.findByText('Runtime applied.')).toBeVisible()
  expect(saves).toBe(2)
  const requests=fetchMock.mock.calls.filter(([url]) => url === '/api/settings/env/apply')
  expect(requests.map(([,options]) => JSON.parse(options.body).expected_env_revision)).toEqual(['a'.repeat(64),'b'.repeat(64)])
})
