import {render, screen, fireEvent} from '@testing-library/react'
import PixelPreviewHistory from './PixelPreviewHistory'

const first = {siteId:'site-' + 'a'.repeat(24), relativeDirectory:'original'}
const second = {siteId:'site-' + 'b'.repeat(24), relativeDirectory:'edited'}
const restored = {...first, relativeDirectory:'restored'}

it('treats a restored content-addressed snapshot as the latest publication', () => {
  const onSelect = vi.fn()
  render(<PixelPreviewHistory previews={[first, second, restored]} selected={second} onSelect={onSelect}/>)
  const options = screen.getAllByRole('option')
  expect(options).toHaveLength(2)
  expect(options.at(-1)).toHaveTextContent('restored · Latest retained')
  fireEvent.click(screen.getByRole('button', {name:'Show latest publication'}))
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(restored)
})

it('does not call the currently restored snapshot an earlier publication', () => {
  render(<PixelPreviewHistory previews={[first, second, restored]} selected={restored} onSelect={vi.fn()}/>)
  expect(screen.queryByText('Viewing an earlier saved publication.')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', {name:'Show latest publication'})).not.toBeInTheDocument()
})

it('keeps an unlisted selected publication selectable while returning to the latest retained snapshot', () => {
  const onSelect = vi.fn()
  const outside = {...first, siteId:'site-' + 'c'.repeat(24)}
  render(<PixelPreviewHistory previews={[first, second, restored]} selected={outside} onSelect={onSelect}/>)
  expect(screen.getByRole('combobox')).toHaveValue(outside.siteId)
  fireEvent.click(screen.getByRole('button', {name:'Show latest publication'}))
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(restored)
})
