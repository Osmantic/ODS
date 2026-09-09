import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from './Sidebar'
import { coreRoutes } from '../plugins/core'

vi.mock('./PixelHandoffApproval', () => ({ default: () => <button>Review handoffs</button> }))
vi.mock('./ODSLogo', () => ({ default: () => <div data-testid="ods-logo" /> }))
afterEach(cleanup)
const show = path => render(<MemoryRouter initialEntries={[path]}><Sidebar status={{}} collapsed={false} onToggle={() => {}} /></MemoryRouter>)
describe('Pixel workspace navigation', () => {
  it('preserves the metal logo across Pixel navigation without restarting it', () => {
    show('/pixel')
    const logo = screen.getByTestId('ods-logo')
    expect(logo).not.toBeVisible()
    fireEvent.click(screen.getByRole('link', { name: 'Back to ODS' }))
    expect(screen.getByTestId('ods-logo')).toBe(logo)
    expect(logo).toBeVisible()
    expect(screen.queryByRole('link', {name:'Pixel',exact:true})).toBeNull()
    expect(screen.queryByRole('link', {name:'Integrations',exact:true})).toBeNull()
    expect(screen.queryByRole('link', {name:'Remote GPU',exact:true})).toBeNull()
    expect(screen.getByRole('link', {name:'Extensions',exact:true})).toBeVisible()
  })
  it('keeps the ODS dashboard separate from the conversational home', () => {
    expect(coreRoutes.find(route => route.id === 'dashboard').path).toBe('/dashboard')
    expect(coreRoutes.find(route => route.id === 'home').path).toBe('/')
    show('/')
    expect(screen.getByTestId('ods-logo')).toBeInTheDocument()
    expect(screen.queryByText('ODS')).toBeNull()
    expect(screen.getByRole('link', { name: 'Dashboard' }).getAttribute('href')).toBe('/dashboard')
    expect(screen.getByRole('button', {name: 'New task'})).toBeVisible()
    expect(screen.getByText('Playground')).toBeVisible()
    expect(screen.getByText('Recent')).toBeVisible()
  })
  it('switches to Pixel controls and retains an exit to ODS', () => {
    show('/pixel')
    expect(screen.getByRole('link', { name: 'Back to ODS' }).getAttribute('href')).toBe('/')
    expect(screen.getByRole('link', { name: 'Settings' }).getAttribute('href')).toBe('/pixel/settings')
    expect(screen.queryByRole('link', { name: 'Extensions' })).toBeNull()
    expect(screen.getByText('Projects')).toBeTruthy()
  })
  it('filters navigation without submitting an agent request', () => {
    show('/')
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.change(screen.getByLabelText('Search navigation'), { target: { value: 'Models' } })
    expect(screen.getByRole('link', { name: 'Models' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Extensions' })).toBeNull()
  })
  it('clears the hidden filter when closing search with Escape', () => {
    show('/')
    fireEvent.click(screen.getByRole('button', {name:'Search'}))
    fireEvent.change(screen.getByLabelText('Search navigation'), {target:{value:'Models'}})
    fireEvent.keyDown(screen.getByLabelText('Search navigation'), {key:'Escape'})
    expect(screen.queryByLabelText('Search navigation')).toBeNull()
    expect(screen.getByRole('link', {name:'Extensions'})).toBeInTheDocument()
    expect(screen.getByRole('button', {name:'Search'})).toHaveFocus()
  })
  it('requests the existing guarded new-task action', () => {
    const handler = vi.fn()
    window.addEventListener('ods:pixel-new-task', handler)
    show('/pixel')
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    expect(handler).toHaveBeenCalledTimes(1)
    window.removeEventListener('ods:pixel-new-task', handler)
  })
})
