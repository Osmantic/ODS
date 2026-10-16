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

const selectSection = (name) => {
  const trigger = screen.getByRole('combobox', { name: 'Settings section' })
  fireEvent.click(trigger)
  fireEvent.click(screen.getByRole('option', { name: new RegExp(name, 'i') }))
}

it('renders inline, filters sections, and preserves form state across navigation', () => {
  const show = vi.spyOn(HTMLDialogElement.prototype, 'showModal').mockImplementation(function () { this.open = true })
  render(<SettingsModal onClose={() => {}} />)
  expect(show).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('region', {name:'ODS settings'})).toBeVisible()
  expect(screen.queryByRole('heading', {name:'General',exact:true})).toBeNull()
  expect(screen.getByRole('combobox', {name:'Settings section'})).toBeVisible()
  fireEvent.change(screen.getByLabelText('Draft'), {target:{value:'Unsaved'}})
  selectSection('Storage')
  expect(screen.getByText('storage content')).toBeVisible()
  expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved')
  fireEvent.change(screen.getByLabelText('Search settings'), {target:{value:'portal'}})
  fireEvent.click(screen.getByRole('combobox', {name:'Settings section'}))
  expect(screen.queryByRole('option', {name:/Storage/i})).toBeNull()
  expect(screen.getByRole('option', {name:/Portal access/i})).toBeInTheDocument()
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
  selectSection('Portal mascot')
  expect(screen.getByRole('heading',{name:'Portal mascot'})).toBeVisible()
  expect(screen.getByLabelText('Sleep after inactivity')).toBeVisible()
  expect(screen.getByRole('button',{name:'Thinking',exact:true})).toHaveAttribute('aria-pressed','true')
})

it('opens the editable profile section without losing another settings draft',()=>{
  render(<SettingsModal/>)
  fireEvent.change(screen.getByLabelText('Draft'),{target:{value:'Unsaved'}})
  selectSection('Profile')
  expect(screen.getByRole('textbox',{name:'Display name'})).toBeVisible()
  expect(screen.getByRole('textbox',{name:'Assistant display name'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Save name'})).toBeVisible()
  selectSection('General')
  expect(screen.getByLabelText('Draft')).toHaveValue('Unsaved')
})

it('offers integrations and remote GPU inside settings without losing remote drafts', async () => {
  render(<SettingsModal />)
  selectSection('Remote model')
  const draft = await screen.findByLabelText('Remote draft')
  fireEvent.change(draft, {target:{value:'Unsaved remote'}})
  selectSection('Service map')
  expect(await screen.findByText('Integration details')).toBeVisible()
  expect(draft).not.toBeVisible()
  selectSection('Remote model')
  expect(draft).toBeVisible()
  expect(draft).toHaveValue('Unsaved remote')
})

it('finds the remote model section by what people search for', () => {
  render(<SettingsModal />)
  for (const query of ['api', 'API key', 'provider', 'gpu']) {
    fireEvent.change(screen.getByLabelText('Search settings'), {target:{value:query}})
    fireEvent.click(screen.getByRole('combobox', {name:'Settings section'}))
    expect(screen.getByRole('option', {name:/Remote model/i})).toBeInTheDocument()
    fireEvent.click(screen.getByRole('combobox', {name:'Settings section'}))
  }
})

it('preserves accurate trigger state during search and reserves empty label for zero matches', () => {
  render(<SettingsModal />)
  const trigger = screen.getByRole('combobox', { name: 'Settings section' })
  expect(trigger).toHaveTextContent('General')

  // From General, type 'storage' into Search settings:
  fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'storage' } })

  // Trigger before selecting a result: must not say 'No matching categories'
  expect(trigger).not.toHaveTextContent('No matching categories')
  expect(trigger).toHaveTextContent('Select section')

  // Opening the combobox shows Storage:
  fireEvent.click(trigger)
  const storageOption = screen.getByRole('option', { name: /Storage/i })
  expect(storageOption).toBeInTheDocument()

  // Select the result:
  fireEvent.click(storageOption)
  expect(trigger).toHaveTextContent('Storage')
  expect(screen.getByText('storage content')).toBeVisible()

  // Zero-match case:
  fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'nonexistent-category' } })
  expect(trigger).toHaveTextContent('No matching categories')
  expect(trigger).toBeDisabled()
  expect(screen.getByText('No settings found.')).toBeVisible()
})
