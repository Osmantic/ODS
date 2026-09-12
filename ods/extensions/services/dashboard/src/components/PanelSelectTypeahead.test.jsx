import { fireEvent, render, screen } from '@testing-library/react'
import PanelSelect from './PanelSelect'

const options = ['Configuration', 'GPU backend', 'GPU memory', 'Network'].map(label => ({ value: label, label }))

test('finds a category by typed prefix without committing until Enter', () => {
  const onChange = vi.fn()
  render(<PanelSelect label="Category" options={options} value="Configuration" onChange={onChange} />)
  const select = screen.getByRole('combobox')
  select.focus()
  for (const key of 'net') fireEvent.keyDown(select, { key })
  expect(select).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'Network' }).id)
  expect(onChange).not.toHaveBeenCalled()
  expect(select).toHaveFocus()
  fireEvent.keyDown(select, { key: 'Enter' })
  expect(onChange).toHaveBeenCalledWith('Network')
})

test('cycles repeated initials and leaves IME or modified shortcuts alone', () => {
  render(<PanelSelect label="Category" options={options} value="Configuration" onChange={vi.fn()} />)
  const select = screen.getByRole('combobox')
  fireEvent.keyDown(select, { key: 'n', ctrlKey: true })
  fireEvent.keyDown(select, { key: 'n', isComposing: true })
  expect(select).toHaveAttribute('aria-expanded', 'false')
  fireEvent.keyDown(select, { key: 'g' })
  expect(select).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'GPU backend' }).id)
  fireEvent.keyDown(select, { key: 'g' })
  expect(select).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'GPU memory' }).id)
  fireEvent.keyDown(select, { key: 'Escape' })
  fireEvent.keyDown(select, { key: 'n' })
  expect(select).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'Network' }).id)
})
