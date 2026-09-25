import {render, screen, fireEvent} from '@testing-library/react'
import PixelPreviewViewport from './PixelPreviewViewport'

const access = {frameUrl:'/pixel-preview/site-'+'a'.repeat(24)+'/__ods_view__.html',sandbox:'allow-scripts allow-forms allow-downloads',route:'dashboard-relay'}
it('changes actual frame dimensions without remounting or changing its authenticated route', () => {
  render(<PixelPreviewViewport access={access} title="Interactive preview" hidden={false}/>)
  const frame = screen.getByTitle('Interactive preview')
  expect(frame.style.width).toBe('100%')
  fireEvent.change(screen.getByLabelText('Preview viewport size'), {target:{value:'phone'}})
  expect(frame.style.width).toBe('375px')
  expect(frame.style.height).toBe('667px')
  fireEvent.click(screen.getByRole('button', {name:'Rotate viewport'}))
  expect(frame.style.width).toBe('667px')
  expect(frame.style.height).toBe('375px')
  expect(screen.getByTitle('Interactive preview')).toBe(frame)
  expect(frame).toHaveAttribute('sandbox',access.sandbox)
  expect(frame).toHaveAttribute('src',access.frameUrl)
  expect(frame).toHaveAttribute('referrerpolicy','no-referrer')
})
it('retains the frame across inspector hiding and returns to fluid panel sizing', () => {
  const {rerender} = render(<PixelPreviewViewport access={access} title="Interactive preview" hidden={false}/>)
  fireEvent.change(screen.getByLabelText('Preview viewport size'), {target:{value:'desktop'}})
  const frame = screen.getByTitle('Interactive preview')
  expect(frame.style.width).toBe('1280px')
  rerender(<PixelPreviewViewport access={access} title="Interactive preview" hidden/>)
  expect(frame).toHaveAttribute('hidden')
  expect(screen.queryByRole('region', {name:'Preview viewport'})).toBeNull()
  rerender(<PixelPreviewViewport access={access} title="Interactive preview" hidden={false}/>)
  expect(screen.getByTitle('Interactive preview')).toBe(frame)
  expect(frame.style.width).toBe('1280px')
  fireEvent.change(screen.getByLabelText('Preview viewport size'), {target:{value:'fit'}})
  expect(frame.style.width).toBe('100%')
  expect(screen.getByRole('button', {name:'Rotate viewport'})).toBeDisabled()
})
