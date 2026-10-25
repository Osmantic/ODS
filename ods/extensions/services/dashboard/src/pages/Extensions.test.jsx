import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
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

it.each([false, true])('shows LiteLLM as an API service without an unsupported Admin UI launch (compact=%s)', async compact => {
  installFetchMock({
    agent_available: true,
    extensions: [
      { id: 'litellm', name: 'LiteLLM (API Gateway)', source: 'core', status: 'enabled', port: 4000, ui_path: '/ui/', features: [baseFeature] },
      { id: 'it-tools', name: 'IT-Tools', source: 'user', status: 'enabled', port: 8080, external_port: 8089, ui_path: '/', features: [baseFeature] },
    ],
    summary: baseSummary({ total: 2, installed: 2 }),
  })
  render(<Extensions compact={compact} />)
  const gateway = within((await screen.findByText('LiteLLM (API Gateway)')).closest('article'))
  expect(gateway.getByText('API service')).toBeVisible()
  expect(gateway.queryByRole('link')).toBeNull()
  expect(gateway.getByRole('button', { name: 'Logs' })).toBeEnabled()

  // Actual web applications retain their published launch port.
  const webApp = within(screen.getByText('IT-Tools').closest('article'))
  expect(webApp.getByRole('link', { name: ':8089' })).toHaveAttribute('href', 'http://localhost:8089')
  expect(webApp.getByRole('button', { name: 'Logs' })).toBeEnabled()

  fireEvent.click(gateway.getByRole('button', { name: 'Details for LiteLLM (API Gateway)' }))
  const details = within(screen.getByRole('dialog', { name: 'LiteLLM (API Gateway)' }))
  expect(details.getByText('4000')).toBeVisible()
  expect(details.queryByRole('link')).toBeNull()
  fireEvent.click(details.getByRole('button', { name: 'Close extension details' }))
  expect(gateway.getByRole('button', { name: 'Logs' })).toBeEnabled()
})

it.each([false, true])('an initial catalog timeout is unavailable, not an empty result, and Retry recovers (compact=%s)', async compact => {
  const catalog = {agent_available: true, extensions: [{id: 'known', name: 'Known extension', status: 'not_installed', features: [baseFeature]}], summary: baseSummary()}
  const fetchMock = installFetchMock(catalog)
  fetchMock.mockRejectedValueOnce(new DOMException('timed out', 'AbortError'))
  render(<Extensions compact={compact} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Extension catalog unavailable')
  expect(screen.queryByRole('button', {name: 'All 0'})).toBeNull()
  expect(screen.queryByText('No extensions match')).toBeNull()
  expect(screen.queryByText(/Try adjusting your search/)).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Retry'}))
  expect(await screen.findByText('Known extension')).toBeVisible()
  expect(screen.getByRole('button', {name: 'All 1'})).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(fetchMock.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
})

it('a failed refresh retains the accepted catalog and its counts', async () => {
  const catalog = {agent_available: true, extensions: [{id: 'known', name: 'Known extension', status: 'not_installed', features: [baseFeature]}], summary: baseSummary()}
  const fetchMock = installFetchMock(catalog)
  render(<Extensions compact />)
  expect(await screen.findByText('Known extension')).toBeVisible()
  fetchMock.mockRejectedValueOnce(new DOMException('timed out', 'AbortError'))
  fireEvent.click(screen.getByRole('button', {name: /refresh/i}))
  expect(await screen.findByText(/Request timed out/)).toBeVisible()
  expect(screen.getByText('Known extension')).toBeVisible()
  expect(screen.getByRole('button', {name: 'All 1'})).toBeVisible()
  expect(screen.queryByText('Extension catalog unavailable')).toBeNull()
  expect(screen.queryByText('No extensions match')).toBeNull()
})

describe('catalog response ordering', () => {
  const deferred = () => {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
  }
  const catalogWithStatus = status => ({
    agent_available: true,
    extensions: [
      { id: 'it-tools', name: 'IT-Tools', source: 'user', status, features: [baseFeature] },
      { id: 'other', name: 'Other install', source: 'user', status: 'installing', features: [baseFeature] },
    ],
    summary: baseSummary({ total: 2 }),
  })
  const setupRequests = () => {
    const requests = []
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/extensions/catalog') {
        const response = deferred()
        requests.push(response)
        return response.promise
      }
      if (url === '/api/extensions/it-tools/disable' && options.method === 'POST') {
        return makeJsonResponse({ action: 'disabled', message: 'IT-Tools disabled' })
      }
      if (url === '/api/extensions/other/progress') return makeJsonResponse({ status: 'idle' })
      if (url === '/api/webui/selection') return makeJsonResponse({ enabled: false, supported: false })
      if (url === '/api/templates') return makeJsonResponse({ templates: [] })
      throw new Error(`Unmocked fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    return { requests, fetchMock }
  }
  const answer = async (request, status) => {
    await act(async () => request.resolve(makeJsonResponse(catalogWithStatus(status))))
  }
  const disable = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Disable IT-Tools' }))
    await act(async () => fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Disable' })))
  }

  afterEach(() => vi.useRealTimers())

  it.each(['refresh', 'progress'])('ignores an older %s catalog after the disable catalog is accepted', async source => {
    vi.useFakeTimers()
    const { requests, fetchMock } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    if (source === 'refresh') {
      fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    } else {
      // Use the real progress interval and catalog fetch, without waiting three seconds.
      await act(async () => vi.advanceTimersByTimeAsync(3000))
    }
    expect(requests).toHaveLength(2)
    await disable()
    expect(requests).toHaveLength(3)
    await answer(requests[2], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    await answer(requests[1], 'stopped')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Disable IT-Tools' })).toBeNull()
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })

  it('does not let an older failure replace a successful disable refresh with an error', async () => {
    vi.useFakeTimers()
    const { requests } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    await disable()
    await answer(requests[2], 'disabled')
    await act(async () => requests[1].reject(new DOMException('timed out', 'AbortError')))
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    expect(screen.queryByText(/Request timed out/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeEnabled()
  })

  it('accepts a slow background catalog across multiple progress ticks without overlapping catalog reads', async () => {
    vi.useFakeTimers()
    const { requests, fetchMock } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    await act(async () => vi.advanceTimersByTimeAsync(3000))
    expect(requests).toHaveLength(2)
    await act(async () => vi.advanceTimersByTimeAsync(3000))
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/extensions/other/progress')).toHaveLength(2)
    expect(requests).toHaveLength(2)
    await answer(requests[1], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    // Completion releases the slot for the next background health read.
    await act(async () => vi.advanceTimersByTimeAsync(3000))
    expect(requests).toHaveLength(3)
    await answer(requests[2], 'disabled')
  })

  it('does not let progress polls supersede a pending foreground refresh', async () => {
    vi.useFakeTimers()
    const { requests } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    await act(async () => vi.advanceTimersByTimeAsync(6000))
    expect(requests).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeDisabled()
    await answer(requests[1], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeEnabled()
  })

  it.each(['network', 'http'])('releases a failed background %s read for the next progress tick', async failure => {
    vi.useFakeTimers()
    const { requests } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    await act(async () => vi.advanceTimersByTimeAsync(3000))
    await act(async () => {
      if (failure === 'network') requests[1].reject(new Error('offline'))
      else requests[1].resolve(makeJsonResponse({}, { ok: false, status: 503 }))
    })
    expect(screen.getByRole('button', { name: 'Disable IT-Tools' })).toBeVisible()
    expect(screen.queryByText(/Failed to load extensions catalog/)).toBeNull()
    await act(async () => vi.advanceTimersByTimeAsync(3000))
    expect(requests).toHaveLength(3)
    await answer(requests[2], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
  })

  it('keeps the latest refresh busy when an older request finishes first', async () => {
    vi.useFakeTimers()
    const { requests } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    await disable()
    await answer(requests[1], 'stopped')
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeDisabled()
    expect(screen.queryByText('stopped')).toBeNull()
    await answer(requests[2], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeEnabled()
  })

  it('retains the accepted catalog on the latest failure, ignores an older success, and lets manual retry recover', async () => {
    vi.useFakeTimers()
    const { requests } = setupRequests()
    render(<Extensions compact />)
    await answer(requests[0], 'enabled')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    await disable()
    await act(async () => requests[2].reject(new Error('offline')))
    expect(screen.getByText(/Failed to load extensions catalog/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Disable IT-Tools' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'All 2' })).toBeVisible()
    await answer(requests[1], 'stopped')
    expect(screen.queryByText('stopped')).toBeNull()
    expect(screen.getByText(/Failed to load extensions catalog/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Refresh extensions' }))
    expect(requests).toHaveLength(4)
    await answer(requests[3], 'disabled')
    expect(screen.getByRole('button', { name: 'Enable IT-Tools' })).toBeVisible()
    expect(screen.queryByText(/Failed to load extensions catalog/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Refresh extensions' })).toBeEnabled()
  })
})

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
    if (target === '/api/extensions/open-webui/prepare' && options.method === 'POST') {
      return makeJsonResponse({ status: 'ready', service_ids: ['open-webui'] })
    }
    if (target === '/api/webui/selection' && options.method === 'POST') {
      // The image is downloaded first; adding then starts from the local image.
      expect(fetchMock).toHaveBeenCalledWith('/api/extensions/open-webui/prepare', expect.objectContaining({ method: 'POST' }))
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

it.each([false, true])('search normalizes hyphens, underscores, and whitespace for extensions and collections (compact=%s)', async compact => {
  const fetchMock = installFetchMock({agent_available:true,extensions:[
    {id:'it-tools',name:'IT-Tools',description:'A collection of handy online tools',status:'not_installed',features:[baseFeature]},
    {id:'other',name:'Other app',description:'Unrelated utility',status:'enabled',features:[baseFeature]},
  ],summary:baseSummary({total:2,installed:1,not_installed:1})}, [
    {id:'it-tools-collection',name:'IT_Tools Starter',description:'Handy developer utilities',services:['it-tools']},
  ])
  render(<Extensions compact={compact}/>)
  expect(await screen.findByRole('heading',{name:'IT-Tools'})).toBeVisible()
  const search = screen.getByRole('textbox')
  fireEvent.change(search,{target:{value:'IT Tools'}})
  expect(screen.getByText('IT-Tools')).toBeVisible()
  expect(screen.queryByText('Other app')).toBeNull()
  fireEvent.change(search,{target:{value:'  it_TOOLS  '}})
  expect(screen.getByText('IT-Tools')).toBeVisible()
  expect(screen.queryByText('Other app')).toBeNull()
  fireEvent.change(search,{target:{value:'handy online'}})
  expect(screen.getByText('IT-Tools')).toBeVisible()
  expect(screen.queryByText('Other app')).toBeNull()
  fireEvent.change(search,{target:{value:'unrelated'}})
  expect(screen.queryByText('IT-Tools')).toBeNull()
  expect(screen.getByText('Other app')).toBeVisible()
  fireEvent.change(search,{target:{value:'it tools'}})
  fireEvent.click(screen.getByRole('button',{name:'Starter collections 1'}))
  expect(screen.getByText('IT_Tools Starter')).toBeVisible()
  fireEvent.change(search,{target:{value:'unrelated'}})
  expect(screen.queryByText('IT_Tools Starter')).toBeNull()
  expect(screen.getByText('No collections match')).toBeVisible()
  expect(fetchMock.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
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

describe('built-in provenance and runtime state', () => {
  it.each([
    ['core', 'disabled', false, 'ODS is not currently reporting this service as healthy.'],
    ['user', 'disabled', false, 'Installed but turned off — won’t start on restart'],
    ['core', 'enabled', true, 'Service is running and healthy'],
  ])('describes runtime evidence without inferring installation (source=%s, status=%s)', async (source, status, manageable, description) => {
    installFetchMock({agent_available:true,extensions:[
      {id:'runtime-evidence',name:'Runtime evidence',source,status,library_manageable:manageable,features:[baseFeature]},
    ],summary:baseSummary()})
    render(<Extensions compact />)
    const card = within((await screen.findByRole('heading',{name:'Runtime evidence'})).closest('article'))
    expect(card.getByText(description, {exact:true})).toBeInTheDocument()
    if (source === 'core' && status === 'disabled') {
      expect(card.queryByText(/Installed but turned off|won’t start on restart/)).toBeNull()
    }
  })

  const states = [
    ['disabled', {}, 'disabled'],
    ['enabled', {}, 'enabled'],
    ['installing', {runtime_starting: true}, 'starting'],
    ['setting_up', {}, 'setting up'],
    ['error', {}, 'error'],
  ]
  it.each([false, true].flatMap(compact => states.map(row => [compact, ...row])))(
    'shows runtime state beside built-in provenance (compact=%s, status=%s)', async (compact, status, extra, label) => {
      const ext = {id:'brave-search',name:'Brave Search',source:'core',category:'optional',
        status,features:[{category:'search'}],...extra}
      const fetchMock = installFetchMock({agent_available:true,extensions:[ext],summary:baseSummary()})
      const catalogFetch = fetchMock.getMockImplementation()
      fetchMock.mockImplementation(async (url, options) => String(url).includes('/progress')
        ? makeJsonResponse({status:'idle'})
        : catalogFetch(url, options))
      render(<Extensions compact={compact} />)
      const card = within((await screen.findByRole('heading',{name:'Brave Search'})).closest('article'))
      expect(card.getByText(label, {exact:true})).toBeVisible()
      expect(card.getByText('built-in')).toBeVisible()
      expect(card.queryByText('core', {exact:true})).toBeNull()
      expect(card.queryByRole('button',{name:/^(Enable|Disable|Install|Add)(?: |$)/})).toBeNull()
      if (status === 'disabled') {
        fireEvent.click(screen.getByRole('button',{name:'Installed 1'}))
        const installed = within(screen.getByRole('heading',{name:'Brave Search'}).closest('article'))
        expect(installed.getByText('disabled', {exact:true})).toBeVisible()
        fireEvent.click(installed.getByRole('button',{name:'Details for Brave Search'}))
        const details = within(screen.getByRole('dialog',{name:'Brave Search'}))
        expect(details.getByText('disabled', {exact:true})).toBeVisible()
        expect(details.getByText('optional', {exact:true})).toBeVisible()
      }
      expect(fetchMock.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
    })

  it('preserves optional and third-party actions without inventing built-in provenance', async () => {
    installFetchMock({agent_available:true,extensions:[
      {id:'n8n',name:'Optional built-in',source:'core',status:'disabled',library_manageable:true,library_selected:false,features:[baseFeature]},
      {id:'retained',name:'Retained extension',source:'user',status:'disabled',features:[baseFeature]},
      {id:'available',name:'Available extension',source:'library',status:'not_installed',installable:true,features:[baseFeature]},
    ],summary:baseSummary({total:3})})
    render(<Extensions compact />)
    const optional = within((await screen.findByRole('heading',{name:'Optional built-in'})).closest('article'))
    expect(optional.getByText('built-in')).toBeVisible()
    expect(optional.getByText('disabled', {exact:true})).toBeVisible()
    expect(optional.getByRole('button',{name:'Add Optional built-in'})).toBeEnabled()
    for (const name of ['Retained extension','Available extension']) {
      const card = within(screen.getByRole('heading',{name}).closest('article'))
      expect(card.queryByText('built-in')).toBeNull()
    }
    expect(screen.getByRole('button',{name:'Enable Retained extension'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'Installed 1'})).toBeVisible()
    expect(screen.getByRole('button',{name:'Available 2'})).toBeVisible()
    expect(within(screen.getByRole('heading',{name:'Available extension'}).closest('article')).getByRole('button',{name:'Install'})).toBeEnabled()
  })
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
    if (target.startsWith('/api/extensions/perplexica/prepare') && options.method === 'POST') {
      return makeJsonResponse({status:'ready',service_ids:['perplexica']})
    }
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
    if (target.startsWith('/api/extensions/perplexica/prepare') && options.method === 'POST') {
      return makeJsonResponse({ status: 'ready', service_ids: ['perplexica'] })
    }
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

it('keeps an unselected bundled service with error progress available for retry', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'perplexica',name:'Perplexica (Deep Research)',source:'core',status:'error',
      library_manageable:true,library_selected:false,error_message:'Host agent could not enable the service.',
      features:[baseFeature]},
  ],summary:baseSummary({total:1,error:1})})
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Available 1'}))
  expect(screen.getByRole('button',{name:'Installed 0'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Retry Perplexica (Deep Research)'}))
  expect(screen.getByRole('dialog',{name:'Confirm action'})).toHaveTextContent('Enable Perplexica (Deep Research)?')
  expect(screen.queryByRole('button',{name:'Disable Perplexica (Deep Research)'})).toBeNull()
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
    if (u.endsWith('/api/extensions/n8n/prepare')) return makeJsonResponse({status:'ready',service_ids:['n8n']})
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

    // Runtime status does not grant controls to an unmanaged built-in.
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

it('adds Hermes and browser access together with its declared dependencies', async () => {
  const catalog = {agent_available:true,extensions:[
    {id:'hermes',name:'Hermes Agent',source:'core',status:'disabled',
      library_manageable:true,library_selected:false,external_port_default:0,features:[baseFeature]},
    {id:'hermes-proxy',name:'Hermes Auth Proxy',source:'core',status:'disabled',
      library_manageable:true,library_selected:false,external_port_default:9120,ui_path:'/auth/ods',features:[baseFeature]},
    {id:'searxng',name:'SearXNG',source:'core',status:'disabled',
      library_manageable:true,library_selected:false,features:[baseFeature]},
  ],summary:baseSummary({total:3})}
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === '/api/extensions/catalog') return makeJsonResponse(catalog)
    if (target === '/api/webui/selection') return makeJsonResponse({enabled:false,supported:false})
    if (target === '/api/templates') return makeJsonResponse({templates:[]})
    if (target === '/api/extensions/hermes-proxy/prepare?auto_enable_deps=true' && options.method === 'POST') return makeJsonResponse({status:'ready',service_ids:['searxng','hermes','hermes-proxy']})
    if (target === '/api/extensions/hermes-proxy/enable?auto_enable_deps=true' && options.method === 'POST') {
      return makeJsonResponse({enabled_services:['searxng','hermes','hermes-proxy'],failed_services:[]})
    }
    throw new Error(`Unmocked fetch: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button',{name:'Add Hermes with web access'}))
  expect(screen.getByRole('dialog',{name:'Confirm action'})).toHaveTextContent('required services including SearXNG')
  fireEvent.click(screen.getByRole('button',{name:'Enable'}))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/extensions/hermes-proxy/enable?auto_enable_deps=true', expect.objectContaining({method:'POST'}),
  ))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/extensions/hermes/enable', expect.anything())
  expect(fetchMock).not.toHaveBeenCalledWith('/api/extensions/searxng/enable', expect.anything())
})

it('opens enabled Hermes through its ODS authenticated proxy entry', async () => {
  installFetchMock({agent_available:true,extensions:[
    {id:'hermes',name:'Hermes Agent',source:'core',status:'enabled',
      library_manageable:true,library_selected:true,external_port_default:0,features:[baseFeature]},
    {id:'hermes-proxy',name:'Hermes Auth Proxy',source:'core',status:'enabled',
      library_manageable:true,library_selected:true,external_port:9120,ui_path:'/auth/ods',features:[baseFeature]},
  ],summary:baseSummary({total:2,installed:2})})
  render(<Extensions compact />)
  const agentCard = (await screen.findByRole('heading',{name:'Hermes Agent'})).closest('article')
  expect(within(agentCard).getByRole('link',{name:':9120'})).toHaveAttribute(
    'href','http://localhost:9120/auth/ods')
})

it('re-checks the ODS session before opening Hermes from its card', async () => {
  const fetchMock = installFetchMock({agent_available:true,extensions:[
    {id:'hermes',name:'Hermes Agent',source:'core',status:'enabled',
      library_manageable:true,library_selected:true,external_port_default:0,features:[baseFeature]},
    {id:'hermes-proxy',name:'Hermes Auth Proxy',source:'core',status:'enabled',
      library_manageable:true,library_selected:true,external_port:9120,ui_path:'/auth/ods',features:[baseFeature]},
  ],summary:baseSummary({total:2,installed:2})})
  const catalog = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url, options) => {
    if (url === '/api/auth/verify-session') return makeJsonResponse({}, {ok:false, status:401})
    if (url === '/api/auth/admin-session') return makeJsonResponse({ok:true})
    return catalog(url, options)
  })
  const tab = {opener:window, closed:false, location:{replace:vi.fn()}}
  vi.spyOn(window, 'open').mockReturnValue(tab)
  render(<Extensions compact />)
  const agentCard = (await screen.findByRole('heading',{name:'Hermes Agent'})).closest('article')
  expect(fireEvent.click(within(agentCard).getByRole('link',{name:':9120'}))).toBe(false)
  expect(window.open).toHaveBeenCalledWith('', '_blank')
  await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith('http://localhost:9120/auth/ods'))
  expect(fetchMock).toHaveBeenCalledWith('/api/auth/admin-session', expect.objectContaining({method:'POST'}))
})
