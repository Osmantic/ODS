import { render as renderBase, screen, fireEvent, cleanup, act, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SettingsModal from './SettingsModal'
const render = ui => renderBase(<MemoryRouter>{ui}</MemoryRouter>)
vi.mock('../pages/ServiceMap', () => ({ default: () => <p>Integration details</p> }))
vi.mock('../pages/RemoteProvider', () => ({ default: () => <input aria-label="Remote draft" /> }))

vi.mock('../pages/Settings', () => ({ default: ({activeSection}) => <div><p>{activeSection} content</p><input aria-label="Draft" /></div> }))
vi.mock('./settings/ConnectedApps', () => ({ default: () => <p>Connected apps content</p> }))
beforeEach(() => { HTMLDialogElement.prototype.showModal = function () { this.open = true } })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete HTMLDialogElement.prototype.showModal })

// Whether ODS has connected apps switched on, as the dashboard API answers it.
function connectedApps(enabled) {
  const fetch = vi.fn(async url => {
    if (url !== '/api/pixel/connections/enabled') throw new Error(`unexpected request ${url}`)
    return {ok: true, json: async () => ({enabled})}
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

it('shows Connected apps only while ODS has them switched on', async () => {
  connectedApps(true)
  render(<SettingsModal />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connected apps', exact: true}))
  expect(await screen.findByText('Connected apps content')).toBeVisible()
  expect(screen.getByText('apps content')).not.toBeVisible()
  fireEvent.change(screen.getByLabelText('Search settings'), {target: {value: 'gmail'}})
  expect(screen.getByRole('button', {name: 'Connected apps', exact: true})).toBeVisible()
})

it('hides Connected apps, and a link to them opens General, while switched off', async () => {
  const fetch = connectedApps(false)
  renderBase(<MemoryRouter initialEntries={['/settings?section=apps']}><SettingsModal /></MemoryRouter>)
  expect(await screen.findByText('general content')).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Connected apps', exact: true})).toBeNull()
  expect(screen.queryByText('Connected apps content')).toBeNull()
  expect(fetch).toHaveBeenCalledWith('/api/pixel/connections/enabled')
})

it('keeps the requested apps section on a failed setting read and retries without reloading', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ok: false, status: 503})
    .mockResolvedValue({ok: true, json: async () => ({enabled: true})})
  vi.stubGlobal('fetch', fetch)
  renderBase(<MemoryRouter initialEntries={['/settings?section=apps']}><SettingsModal /></MemoryRouter>)
  expect(await screen.findByText('Connected apps settings are unavailable right now.')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Connected apps', exact: true})).toBeVisible()
  expect(screen.queryByText('general content')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Retry', exact: true}))
  expect(await screen.findByText('Connected apps content')).toBeVisible()
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('rechecks on focus and never lets an older disabled reply replace the newer choice', async () => {
  let older
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => { older = resolve }))
    .mockResolvedValue({ok: true, json: async () => ({enabled: true})})
  vi.stubGlobal('fetch', fetch)
  renderBase(<MemoryRouter initialEntries={['/settings?section=apps']}><SettingsModal /></MemoryRouter>)
  fireEvent(window, new Event('focus'))
  expect(await screen.findByText('Connected apps content')).toBeVisible()
  await act(async () => { older({ok: true, json: async () => ({enabled: false})}) })
  expect(screen.getByText('Connected apps content')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Connected apps', exact: true})).toBeVisible()
})

it('retains a verified enabled section through a later outage, but honors a verified opt-out', async () => {
  let reply = {ok: true, json: async () => ({enabled: true})}
  const fetch = vi.fn(async () => reply)
  vi.stubGlobal('fetch', fetch)
  renderBase(<MemoryRouter initialEntries={['/settings?section=apps']}><SettingsModal /></MemoryRouter>)
  expect(await screen.findByText('Connected apps content')).toBeVisible()
  reply = {ok: false, status: 503}
  fireEvent(window, new Event('focus'))
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  expect(screen.getByText('Connected apps content')).toBeVisible()
  reply = {ok: true, json: async () => ({enabled: false})}
  fireEvent(window, new Event('focus'))
  await waitFor(() => expect(screen.queryByRole('button', {name: 'Connected apps', exact: true})).toBeNull())
  expect(screen.getByText('general content')).toBeVisible()
})

it('calls the model provider settings Portal models, still found by searching connections', () => {
  connectedApps(false)
  render(<SettingsModal />)
  fireEvent.click(screen.getByRole('button', {name: 'Portal models', exact: true}))
  expect(screen.getByText('connections content')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Search settings'), {target: {value: 'connections'}})
  expect(screen.getByRole('button', {name: 'Portal models', exact: true})).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Portal connections'})).toBeNull()
})

it('renders inline, filters sections, and preserves form state across navigation', () => {
  const show = vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () { this.open = true })
  render(<SettingsModal onClose={() => {}} />)
  expect(show).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('region', {name:'ODS settings'})).toBeVisible()
  expect(screen.queryByRole('heading', {name:'General',exact:true})).toBeNull()
  expect(screen.getByRole('button', {name:'General',exact:true})).toBeVisible()
  fireEvent.change(screen.getByLabelText('Draft'), {target:{value:'Unsaved'}})
  fireEvent.click(screen.getByRole('button', {name:'Storage',exact:true}))
  expect(screen.getByText('storage content')).toBeVisible()
  expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved')
  fireEvent.change(screen.getByLabelText('Search settings'), {target:{value:'portal'}})
  expect(screen.queryByRole('button', {name:'Storage',exact:true})).toBeNull()
  expect(screen.getByRole('button', {name:'Portal access'})).toBeVisible()
})

it('leaves closing and collapsing to the workspace panel header', () => {
  vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () { this.open = true })
  const close = vi.fn()
  render(<SettingsModal onClose={close} />)
  expect(screen.queryByRole('button', {name:'Close settings'})).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(close).not.toHaveBeenCalled()
})

it('provides mascot controls in their own settings section',()=>{
  render(<SettingsModal/>)
  fireEvent.click(screen.getByRole('button',{name:'Portal mascot',exact:true}))
  expect(screen.getByRole('heading',{name:'Portal mascot'})).toBeVisible()
  expect(screen.getByLabelText('Sleep after inactivity')).toBeVisible()
  expect(screen.getByRole('button',{name:'Thinking',exact:true})).toHaveAttribute('aria-pressed','true')
})

it('opens the editable profile section without losing another settings draft',()=>{
  render(<SettingsModal/>)
  fireEvent.change(screen.getByLabelText('Draft'),{target:{value:'Unsaved'}})
  fireEvent.click(screen.getByRole('button',{name:'Profile',exact:true}))
  expect(screen.getByRole('textbox',{name:'Display name'})).toBeVisible()
  expect(screen.getByRole('textbox',{name:'Assistant display name'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Save name'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'General',exact:true}))
  expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved')
})

it('offers integrations and remote GPU inside settings without losing remote drafts', async () => {
  render(<SettingsModal />)
  fireEvent.click(screen.getByRole('button', {name:'Remote model',exact:true}))
  const draft = await screen.findByLabelText('Remote draft')
  fireEvent.change(draft, {target:{value:'Unsaved remote'}})
  fireEvent.click(screen.getByRole('button', {name:'Service map',exact:true}))
  expect(await screen.findByText('Integration details')).toBeVisible()
  expect(draft).not.toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'Remote model',exact:true}))
  expect(draft).toBeVisible()
  expect(draft).toHaveValue('Unsaved remote')
})

it('finds the remote model section by what people search for', () => {
  render(<SettingsModal />)
  for (const query of ['api', 'API key', 'provider', 'gpu']) {
    fireEvent.change(screen.getByLabelText('Search settings'), {target:{value:query}})
    expect(screen.getByRole('button', {name:'Remote model',exact:true})).toBeInTheDocument()
  }
})
