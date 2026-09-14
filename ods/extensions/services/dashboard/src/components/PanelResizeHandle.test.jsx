import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import PanelResizeHandle from './PanelResizeHandle'
afterEach(cleanup)

function pointer(element, type, x, button = 0) {
  const event = new MouseEvent(type, {bubbles:true,clientX:x,button})
  Object.defineProperty(event,'pointerId',{value:1})
  fireEvent(element,event)
}

it('bounds pointer dragging and stops resizing after release or cancellation', () => {
  const resize = vi.fn()
  const {container} = render(<main className="portal-workspace"><aside><PanelResizeHandle width={440} onResize={resize}/></aside></main>)
  Object.defineProperty(container.querySelector('main'),'clientWidth',{value:1000})
  container.querySelector('aside').getBoundingClientRect = () => ({width:440})
  const handle = screen.getByRole('separator')
  handle.setPointerCapture = vi.fn()
  handle.hasPointerCapture = () => true
  handle.releasePointerCapture = vi.fn()
  pointer(handle,'pointerdown',560)
  pointer(handle,'pointermove',500)
  expect(resize).toHaveBeenLastCalledWith(500)
  pointer(handle,'pointermove',0)
  expect(resize).toHaveBeenLastCalledWith(680)
  pointer(handle,'pointermove',1000)
  expect(resize).toHaveBeenLastCalledWith(320)
  pointer(handle,'pointerup',1000)
  expect(handle.releasePointerCapture).toHaveBeenCalledWith(1)
  resize.mockClear()
  pointer(handle,'pointermove',600)
  expect(resize).not.toHaveBeenCalled()
  pointer(handle,'pointerdown',560)
  pointer(handle,'pointercancel',560)
  pointer(handle,'pointermove',300)
  expect(resize).not.toHaveBeenCalled()
  pointer(handle,'pointerdown',560,2)
  pointer(handle,'pointermove',300)
  expect(resize).not.toHaveBeenCalled()
})

it('clamps double-click reset to the room available to the preview', () => {
  const resize = vi.fn()
  const {container} = render(<main className="preview-container"><PanelResizeHandle width={240} onResize={resize} container=".preview-container" minimum={240}/></main>)
  Object.defineProperty(container.querySelector('main'),'clientWidth',{value:600})
  fireEvent.doubleClick(screen.getByRole('separator'))
  expect(resize).toHaveBeenCalledWith(280)
})

it('supports keyboard resizing and a reset with bounded widths', () => {
  const resize = vi.fn()
  render(<PanelResizeHandle width={440} onResize={resize} />)
  const handle = screen.getByRole('separator', {name:'Resize workspace panel'})
  fireEvent.keyDown(handle, {key:'ArrowLeft'})
  expect(resize).toHaveBeenLastCalledWith(472)
  fireEvent.keyDown(handle, {key:'ArrowRight'})
  expect(resize).toHaveBeenLastCalledWith(408)
  fireEvent.keyDown(handle, {key:'Home'})
  expect(resize).toHaveBeenLastCalledWith(440)
})
