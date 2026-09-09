import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PixelComposerTools from './PixelComposerTools'

test('commands insert a draft without sending it', () => {
  const insert = vi.fn()
  render(<MemoryRouter><PixelComposerTools input="" disabled={false} onInsert={insert}/></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', {name:'Open prompt commands'}))
  fireEvent.click(screen.getByRole('button', {name:/Plan Milestones/}))
  expect(insert).toHaveBeenCalledWith('Plan this outcome with milestones and exact completion criteria: ')
  expect(screen.queryByRole('group', {name:'Prompt commands'})).not.toBeInTheDocument()
})
test('slash opens commands and Escape returns focus to the trigger', () => {
  render(<MemoryRouter><PixelComposerTools input="" disabled={false} onInsert={() => {}}/></MemoryRouter>)
  const button = screen.getByRole('button', {name:'Open prompt commands'})
  fireEvent.click(button)
  fireEvent.keyDown(window, {key:'Escape'})
  expect(button).toHaveFocus()
  expect(button).toHaveAttribute('aria-expanded','false')
})
test('does not allow draft changes while work is active', () => {
  render(<MemoryRouter><PixelComposerTools input="/" disabled onInsert={() => {}}/></MemoryRouter>)
  expect(screen.getByRole('button', {name:'Open prompt commands'})).toBeDisabled()
  expect(screen.queryByRole('group', {name:'Prompt commands'})).not.toBeInTheDocument()
  expect(screen.getByTitle('Pixel access settings')).toHaveAttribute('href','/pixel/settings?section=access')
})
