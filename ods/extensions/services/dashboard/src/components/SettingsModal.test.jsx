import { render as renderBase, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import SettingsModal from './SettingsModal'
const render = ui => renderBase(<MemoryRouter>{ui}</MemoryRouter>)
vi.mock('../pages/ServiceMap', () => ({ default: () => <p>Integration details</p> }))
vi.mock('../pages/RemoteProvider', () => ({ default: () => <input aria-label="Remote draft" /> }))

vi.mock('../pages/Settings', () => ({ default: ({activeSection}) => <div><p>{activeSection} content</p><input aria-label="Draft" /></div> }))
beforeEach(() => { HTMLDialogElement.prototype.showModal = function () { this.open = true } })
afterEach(() => { cleanup(); vi.restoreAllMocks(); delete HTMLDialogElement.prototype.showModal })

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
  fireEvent.change(screen.getByLabelText('Search settings'), {target:{value:'pixel'}})
  expect(screen.queryByRole('button', {name:'Storage',exact:true})).toBeNull()
  expect(screen.getByRole('button', {name:'Pixel access'})).toBeVisible()
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
  fireEvent.click(screen.getByRole('button', {name:'Remote GPU',exact:true}))
  const draft = await screen.findByLabelText('Remote draft')
  fireEvent.change(draft, {target:{value:'Unsaved remote'}})
  fireEvent.click(screen.getByRole('button', {name:'Integrations',exact:true}))
  expect(await screen.findByText('Integration details')).toBeVisible()
  expect(draft).not.toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'Remote GPU',exact:true}))
  expect(draft).toBeVisible()
  expect(draft).toHaveValue('Unsaved remote')
})
