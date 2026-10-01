import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * Tests for the Extensions page rendering of unhealthy/installable derivations
 * (PR #1037 added the unhealthy poller + UI surface). Specifically asserts:
 *   - StatusBadge text for unhealthy
 *   - isToggleable — user extensions and qualified optional built-ins only
 *   - showInstall   (Extensions.jsx L628) — not_installed && ext.installable
 *   - Check Logs CTA for unhealthy user extensions
 *
 * Mocks both /api/extensions/catalog and /api/templates because Extensions
 * mounts both fetches in its initial useEffect (lines 162-173); leaving
 * /api/templates unmocked produces an unhandled jsdom rejection.
 */

const makeJsonResponse = (data, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => data,
})

const baseSummary = (overrides = {}) => ({
  total: 1,
  installed: 0,
  stopped: 0,
  unhealthy: 0,
  not_installed: 0,
  installing: 0,
  error: 0,
  incompatible: 0,
  ...overrides,
})

const baseFeature = { category: 'tools', icon: 'Box' }

const installFetchMock = (catalogFixture, templates = [], webuiSelection = { enabled: true, supported: false }) => {
  const fetchMock = vi.fn(async (url) => {
    const u = String(url)
    if (u.includes('/api/extensions/catalog')) return makeJsonResponse(catalogFixture)
    if (u === '/api/webui/selection') return makeJsonResponse(webuiSelection)
    if (u.includes('/api/templates')) return makeJsonResponse({ templates })
    throw new Error(`Unmocked fetch: ${u}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

it('hides unsupported extensions from results, categories and counts without hiding unhealthy services',async()=>{
  installFetchMock({agent_available:true,extensions:[
    {id:'supported',name:'Supported',status:'unhealthy',source:'user',features:[baseFeature]},
    {id:'unsupported',name:'Unsupported device',status:'incompatible',features:[{category:'hidden-category'}]},
    {id:'unsupported-flag',name:'Unsupported flag',status:'not_installed',compatible:false,features:[]},
  ],summary:baseSummary({total:3})})
  render(<Extensions compact/> )
  expect(await screen.findByText('Supported')).toBeVisible()
  expect(screen.queryByText('Unsupported device')).toBeNull()
  expect(screen.queryByText('Unsupported flag')).toBeNull()
  expect(screen.queryByRole('option',{name:'hidden-category'})).toBeNull()
  expect(screen.getByRole('button',{name:'All 1'})).toBeVisible()
})

it('adds Open WebUI from the available library without offering generic core controls', async () => {
  const catalog = {
    agent_available: true,
    extensions: [{ id: 'open-webui', name: 'Open WebUI', source: 'core', status: 'disabled', features: [baseFeature], description: 'Chat service' }],
    summary: baseSummary({ total: 1, installed: 1 }),
  }
  let enabled = false
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === '/api/extensions/catalog') return makeJsonResponse(catalog)
    if (target === '/api/webui/selection' && options.method === 'POST') {
      expect(JSON.parse(options.body)).toEqual({ enabled: true })
      enabled = true
      return makeJsonResponse({ enabled: true, action: 'enabled' })
    }
    if (target === '/api/webui/selection') return makeJsonResponse({ enabled, supported: true })
    if (target === '/api/templates') return makeJsonResponse({ templates: [] })
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  expect(await screen.findByRole('button', { name: 'Available 1' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Available 1' }))
  expect(screen.getByRole('button', { name: 'Add Open WebUI' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Enable Open WebUI' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Add Open WebUI' }))
  const dialog = screen.getByRole('dialog', { name: 'Confirm action' })
  expect(dialog).toHaveTextContent('Add Open WebUI')
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/webui/selection', expect.objectContaining({ method: 'POST' })))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Available 0' })).toBeVisible())
})

it('does not offer WebUI add-back when the host does not support it', async () => {
  installFetchMock({
    agent_available: true,
    extensions: [{ id: 'open-webui', name: 'Open WebUI', source: 'core', status: 'disabled', features: [baseFeature] }],
    summary: baseSummary({ total: 1, installed: 1 }),
  })
  render(<Extensions compact />)
  expect(await screen.findByText('Open WebUI')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Add Open WebUI' })).toBeNull()
})

it('hides collections that require unsupported services while retaining usable collections', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'unsupported',name:'GPU unavailable',status:'incompatible',features:[]},
    {id:'supported',name:'Ready to install',status:'not_installed',features:[]},
  ]}, [
    {id:'blocked',name:'Blocked collection',services:['unsupported']},
    {id:'available',name:'Available collection',services:['supported']},
  ])
  render(<Extensions compact/>)
  fireEvent.click(await screen.findByRole('button',{name:'Starter collections 1'}))
  expect(screen.getByText('Available collection')).toBeVisible()
  expect(screen.queryByText('Blocked collection')).toBeNull()
})

// Find the per-extension toggle <button> by its uniquely-shaped width class.
// L680 uses Tailwind arbitrary values: `inline-flex h-[18px] w-[32px] ...`
// — the only button on the card with that footprint is the toggle.
const findToggleButton = (container) =>
  Array.from(container.querySelectorAll('button')).find((b) =>
    b.className.includes('w-[32px]')
  )

beforeEach(() => {
  vi.useRealTimers()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('offers Add for qualified bundled n8n while keeping other built-ins managed by ODS', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'n8n',name:'n8n (Workflows)',source:'core',status:'disabled',library_manageable:true,library_selected:false,features:[baseFeature]},
    {id:'hermes',name:'Hermes',source:'core',status:'disabled',features:[baseFeature]},
    {id:'dashboard',name:'Dashboard',source:'core',status:'enabled',features:[baseFeature]},
  ],summary:baseSummary({total:3})})
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Add n8n (Workflows)'}))
  expect(screen.getByRole('dialog',{name:'Confirm action'})).toHaveTextContent('Enable n8n (Workflows)?')
  expect(screen.queryByRole('button',{name:'Add Hermes'})).toBeNull()
  expect(screen.queryByRole('button',{name:'Enable Hermes'})).toBeNull()
  expect(screen.queryByRole('button',{name:'Disable Dashboard'})).toBeNull()
})

it('shows bundled Perplexica in Available and asks before adding SearXNG', async () => {
  const catalog = {agent_available:true,extensions:[
    {id:'perplexica',name:'Perplexica (Deep Research)',source:'core',status:'disabled',
      library_manageable:true,library_selected:false,features:[baseFeature]},
    {id:'n8n',name:'n8n (Workflows)',source:'core',status:'disabled',
      library_manageable:true,library_selected:false,features:[baseFeature]},
  ],summary:baseSummary({total:2})}
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === '/api/extensions/catalog') return makeJsonResponse(catalog)
    if (target === '/api/webui/selection') return makeJsonResponse({enabled:true,supported:false})
    if (target === '/api/templates') return makeJsonResponse({templates:[]})
    if (target === '/api/extensions/perplexica/enable' && options.method === 'POST') {
      return makeJsonResponse({detail:{missing_dependencies:['searxng']}}, {ok:false,status:400})
    }
    if (target === '/api/extensions/perplexica/enable?auto_enable_deps=true' && options.method === 'POST') {
      return makeJsonResponse({message:'Perplexica and SearXNG selected'})
    }
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Available 2'}))
  expect(screen.getByRole('button',{name:'Add Perplexica (Deep Research)'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Add n8n (Workflows)'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Installed 0'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Add Perplexica (Deep Research)'}))
  fireEvent.click(screen.getByRole('button',{name:'Enable'}))
  expect(await screen.findByRole('dialog',{name:'Enable dependencies'})).toHaveTextContent('searxng')
  expect(fetchMock).toHaveBeenCalledWith('/api/extensions/perplexica/enable', expect.objectContaining({method:'POST'}))
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/extensions/perplexica/enable?auto_enable_deps=true', expect.anything())
  fireEvent.click(screen.getByRole('button',{name:'Add Perplexica (Deep Research)'}))
  fireEvent.click(screen.getByRole('button',{name:'Enable'}))
  fireEvent.click(await screen.findByRole('button',{name:'Enable All'}))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/extensions/perplexica/enable?auto_enable_deps=true',
    expect.objectContaining({method:'POST'}),
  ))
})

it('refreshes healthy dependency cards after Enable All when progress is idle', async () => {
  let selected = false
  let progressCalls = 0
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === '/api/extensions/catalog') {
      const status = !selected ? 'disabled' : progressCalls ? 'enabled' : 'stopped'
      return makeJsonResponse({ agent_available: true, extensions: ['perplexica', 'searxng'].map(id => ({
        id, name: id === 'perplexica' ? 'Perplexica (Deep Research)' : 'SearXNG',
        source: 'core', status, library_manageable: true, library_selected: selected,
        features: [baseFeature],
      })), summary: baseSummary({ total: 2 }) })
    }
    if (target === '/api/webui/selection') return makeJsonResponse({ enabled: false, supported: false })
    if (target === '/api/templates') return makeJsonResponse({ templates: [] })
    if (target === '/api/extensions/perplexica/enable' && options.method === 'POST') {
      return makeJsonResponse({ detail: { missing_dependencies: ['searxng'] } }, { ok: false, status: 400 })
    }
    if (target === '/api/extensions/perplexica/enable?auto_enable_deps=true' && options.method === 'POST') {
      selected = true
      return makeJsonResponse({ enabled_services: ['searxng', 'perplexica'], failed_services: [] })
    }
    if (target === '/api/extensions/perplexica/progress') {
      progressCalls += 1
      return makeJsonResponse({ status: 'idle' })
    }
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Add Perplexica (Deep Research)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Enable All' }))
  expect(await screen.findByRole('button', { name: 'Retry Perplexica (Deep Research)' })).toBeVisible()

  await waitFor(() => {
    expect(screen.queryByRole('button', { name: 'Retry Perplexica (Deep Research)' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Retry SearXNG' })).toBeNull()
    expect(screen.getAllByText('enabled')).toHaveLength(2)
  }, { timeout: 8000 })
  expect(progressCalls).toBeGreaterThan(0)
})

it('lets an errored bundled n8n be retried or disabled without a remove control', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'n8n',name:'n8n (Workflows)',source:'core',status:'error',library_manageable:true,library_selected:true,features:[baseFeature]},
  ],summary:baseSummary({total:1,error:1})})
  render(<Extensions compact />)
  expect(await screen.findByRole('button',{name:'Retry n8n (Workflows)'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Disable n8n (Workflows)'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Remove n8n (Workflows)'})).toBeNull()
})

it('makes an OpenCode setup retry an explicit install action', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'opencode',name:'OpenCode',source:'core',status:'error',installable:true,
      app_path:'/apps/opencode',features:[baseFeature]},
  ],summary:baseSummary({total:1,error:1})})
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Retry'}))
  expect(screen.getByRole('dialog',{name:'Confirm action'})).toHaveTextContent('Install OpenCode? This will download and start the service.')
})

it('reports a failed bundled n8n start and refreshes to a retryable card', async () => {
  const timeoutSpy = vi.spyOn(globalThis.AbortSignal, 'timeout').mockReturnValue(new AbortController().signal)
  const ext = {id:'n8n',name:'n8n (Workflows)',source:'core',status:'disabled',
    library_manageable:true,library_selected:false,features:[baseFeature]}
  const fetchMock = vi.fn(async (url) => {
    const u = String(url)
    if (u.includes('/api/extensions/catalog')) {
      return makeJsonResponse({agent_available:true,extensions:[ext],
        summary:baseSummary({total:1,error:ext.status === 'error' ? 1 : 0})})
    }
    if (u.includes('/api/templates')) return makeJsonResponse({templates:[]})
    if (u.endsWith('/api/extensions/n8n/enable')) {
      Object.assign(ext,{status:'error',library_selected:true,error_message:'Host agent failed to start extension.'})
      return makeJsonResponse({enabled_services:['n8n'],failed_services:['n8n'],restart_required:true})
    }
    throw new Error(`Unmocked fetch: ${u}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Add n8n (Workflows)'}))
  fireEvent.click(screen.getByRole('button',{name:'Enable'}))
  expect(await screen.findByText(/was selected but did not start/)).toBeVisible()
  expect(await screen.findByRole('button',{name:'Retry n8n (Workflows)'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Disable n8n (Workflows)'})).toBeVisible()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/extensions/catalog')).length)
    .toBeGreaterThanOrEqual(2)
  expect(timeoutSpy).toHaveBeenCalledWith(13 * 60 * 1000)
})

describe('Extensions page — unhealthy + install derivations', () => {
  it('shows starter collections as a matching paginated library with an explicit preview', async () => {
    vi.stubGlobal('fetch', vi.fn(async url => String(url).includes('/api/templates')
      ? makeJsonResponse({templates:Array.from({length:8}, (_,index) => ({id:`collection-${index}`,name:`Collection ${index}`,description:'A useful collection',services:['a','b']}))})
      : makeJsonResponse({extensions:[],summary:baseSummary({total:0}),agent_available:true})))
    render(<Extensions compact/>)
    fireEvent.click(await screen.findByRole('button',{name:'Starter collections 8'}))
    expect(screen.getByText('Collection 0')).toBeVisible()
    expect(screen.queryByText('Collection 7')).toBeNull()
    fireEvent.click(screen.getByRole('button',{name:'Page 2'}))
    expect(screen.getByText('Collection 7')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Search extensions'),{target:{value:'Collection 0'}})
    expect(screen.getByText('Collection 0')).toBeVisible()
    expect(screen.queryByRole('button',{name:'Page 2'})).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('filters the compact library through its views without a full status legend', async () => {
    installFetchMock({extensions:[{id:'installed',name:'Installed tool',status:'disabled',source:'user',features:[baseFeature]},{id:'new',name:'New tool',status:'not_installed',source:'user',installable:true,features:[baseFeature]}],summary:baseSummary({total:2,not_installed:1}),agent_available:true})
    render(<Extensions compact />)
    await screen.findByText('New tool')
    expect(screen.queryByText('Status Legend')).toBeNull()
    fireEvent.click(screen.getByRole('button',{name:'Installed 1'}))
    expect(screen.queryByText('New tool')).toBeNull()
    expect(screen.getByText('Installed tool')).toBeVisible()
    fireEvent.click(screen.getByRole('button',{name:'Available 1'}))
    expect(screen.getByText('New tool')).toBeVisible()
    expect(screen.queryByText('Installed tool')).toBeNull()
  })
  it('keeps search and select filters usable in the compact portal panel', async () => {
    installFetchMock({extensions:[{id:'demo',name:'Demo extension',status:'not_installed',source:'user',installable:true,features:[baseFeature],description:'Test'}],summary:baseSummary({not_installed:1}),gpu_backend:'apple',agent_available:true})
    render(<Extensions compact />)
    await screen.findByText('Demo extension')
    expect(screen.getByRole('combobox',{name:'Status'})).toBeVisible()
    expect(screen.getByRole('combobox',{name:'Category'})).toBeVisible()
    fireEvent.change(screen.getByLabelText('Search extensions'),{target:{value:'missing'}})
    expect(screen.queryByText('Demo extension')).toBeNull()
  })
  it('renders theme-neutral unhealthy badge for unhealthy user ext', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'A user extension whose container is running but failing health checks.',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)

    // Card name shows up only after fetchCatalog resolves.
    await screen.findByText('Unhealthy User Service')

    // StatusBadge L594 renders status.replace(/_/g, ' ') — case is preserved,
    // so 'unhealthy' (lowercase) appears in the DOM. CSS uppercases it visually.
    // Disambiguate from the status legend (L383-392, which also renders keys
    // lowercase) by filtering to the badge's `cursor-help` class.
    const matches = screen.getAllByText('unhealthy')
    const badge = matches.find((el) => el.className.includes('cursor-help'))
    expect(badge).toBeTruthy()
    expect(badge.className).toContain('text-theme-text-secondary')
  })

  it('renders toggle switch for unhealthy user ext (isToggleable=true)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // The toggle button is rendered (L676-695) when isToggleable is true.
    await waitFor(() => {
      expect(findToggleButton(container)).toBeTruthy()
    })
  })

  it('renders cli_installed user ext as installed and toggleable', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'aider',
          name: 'Aider',
          status: 'cli_installed',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'CLI-only one-shot extension',
        },
      ],
      summary: baseSummary({ installed: 1, cli_installed: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Aider')

    const matches = screen.getAllByText('cli installed')
    const badge = matches.find((el) => el.className.includes('cursor-help'))
    expect(badge).toBeTruthy()

    const toggle = findToggleButton(container)
    expect(toggle).toBeTruthy()
    expect(toggle.className).toContain('bg-green-500')
    expect(screen.getByText('Disable to remove')).toBeInTheDocument()
  })

  it('does NOT render toggle for unhealthy CORE ext (isToggleable=false because not user)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-core',
          name: 'Unhealthy Core Service',
          status: 'unhealthy',
          source: 'core',
          installable: false,
          features: [baseFeature],
          description: 'A core extension; toggle suppressed regardless of status.',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Unhealthy Core Service')

    // Core extensions render the "CORE" pill (L665-672) instead of StatusBadge
    // and never get a toggle button — isToggleable requires source === 'user'.
    expect(findToggleButton(container)).toBeUndefined()
  })

  it('does NOT render Install button for unhealthy ext (showInstall=false)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: true, // even installable=true must NOT show Install when status != not_installed
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // showInstall = (status === 'not_installed') && ext.installable  → false here.
    // The Install button (L740-749) renders the literal text " Install".
    // queryByText is exact-by-default; "Installed"/"Installing" labels in the
    // summary bar / status filters won't match.
    expect(screen.queryByText('Install')).toBeNull()
  })

  it('renders Install button for not_installed + installable (showInstall=true)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-installable',
          name: 'Installable Service',
          status: 'not_installed',
          source: 'user',
          installable: true,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ not_installed: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Installable Service')

    expect(screen.getByText('Install')).toBeInTheDocument()
  })

  it('renders Check Logs CTA for unhealthy user ext', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // L760-769: Check Logs button rendered when isUserExt && isUnhealthy.
    expect(screen.getByRole('button', { name: /Check Logs/i })).toBeInTheDocument()
  })

  it('renders LLM swap-safety badges from the catalog contract', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'safe-llm-app',
          name: 'Safe LLM App',
          status: 'enabled',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
          llm: {
            consumes: true,
            route: 'gateway',
            pinning: 'none',
            swap_safe: true,
            swap_safe_reason: 'Routes through the ODS gateway alias.',
          },
        },
        {
          id: 'unsafe-llm-app',
          name: 'Unsafe LLM App',
          status: 'enabled',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
          llm: {
            consumes: true,
            route: 'direct',
            pinning: 'none',
            swap_safe: false,
            swap_safe_reason: 'Direct model route without a declared refresh path.',
          },
        },
      ],
      summary: baseSummary({ installed: 2, enabled: 2, total: 2 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Safe LLM App')

    expect(screen.getByText('Swap-safe')).toBeInTheDocument()
    expect(screen.getByText('Not swap-safe')).toBeInTheDocument()
  })

  it('confirms a modified library update with the force contract', async () => {
    const timeoutSpy = vi.spyOn(globalThis.AbortSignal, 'timeout').mockReturnValue(new AbortController().signal)
    const catalog = {
      extensions: [{
        id: 'tracked-ext',
        name: 'Tracked Extension',
        status: 'enabled',
        source: 'user',
        installable: true,
        update_available: true,
        update_status: 'modified',
        locally_modified: true,
        rollback_available: false,
        features: [baseFeature],
        description: 'desc',
      }],
      summary: baseSummary({ installed: 1, enabled: 1, updates_available: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    }
    const fetchMock = vi.fn(async (url) => {
      const target = String(url)
      if (target.includes('/api/extensions/catalog')) return makeJsonResponse(catalog)
      if (target.includes('/api/templates')) return makeJsonResponse({ templates: [] })
      if (target === '/api/extensions/tracked-ext/update?force=true') {
        return makeJsonResponse({ action: 'updated', message: 'Extension updated.' })
      }
      throw new Error(`Unmocked fetch: ${target}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Extensions />)
    await screen.findByText('Tracked Extension')
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    expect(screen.getByText(/Local definition changes will be replaced/)).toBeInTheDocument()
    const updateButtons = screen.getAllByRole('button', { name: 'Update' })
    fireEvent.click(updateButtons[updateButtons.length - 1])

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/extensions/tracked-ext/update?force=true',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(timeoutSpy).toHaveBeenCalledWith(30 * 60 * 1000)
  })

  it('shows rollback when a previous extension definition is available', async () => {
    installFetchMock({
      extensions: [{
        id: 'rollback-ext',
        name: 'Rollback Extension',
        status: 'disabled',
        source: 'user',
        installable: true,
        update_available: false,
        update_status: 'current',
        locally_modified: false,
        rollback_available: true,
        features: [baseFeature],
        description: 'desc',
      }],
      summary: baseSummary({ installed: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Rollback Extension')
    expect(screen.getByRole('button', { name: 'Rollback' })).toBeInTheDocument()
  })
})
