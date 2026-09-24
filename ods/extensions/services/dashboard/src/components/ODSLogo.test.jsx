import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, act } from '@testing-library/react'
import ODSLogo from './ODSLogo'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function loadedImage() {
  vi.stubGlobal('Image', class {
    naturalWidth = 2
    naturalHeight = 1
    set src(_value) { this.onload() }
  })
}

it('keeps the supplied logo available when 2D canvas is unavailable', () => {
  loadedImage()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  const {container} = render(<ODSLogo />)
  expect(container.querySelector('img')).toHaveAttribute('src', '/osmantic-isolated-os.png')
  expect(container.firstChild).not.toHaveAttribute('data-mask-ready')
})

it.each(['context','readback'])('retains the original silhouette when canvas %s is denied', denial => {
  loadedImage()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => {
    if (denial === 'context') throw new Error('Canvas disabled')
    return {drawImage:vi.fn(),getImageData:() => {throw new Error('Readback denied')}}
  })
  const {container} = render(<ODSLogo />)
  expect(container.querySelector('img')).toHaveAttribute('src', '/osmantic-isolated-os.png')
  expect(container.firstChild).not.toHaveAttribute('data-mask-ready')
})

it('preserves the chroma mask without requiring WebGL or starting an animation', () => {
  loadedImage()
  const pixels = new Uint8ClampedArray([255,0,0,255,20,20,20,255])
  const putImageData = vi.fn()
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    drawImage:vi.fn(),getImageData:() => ({data:pixels}),putImageData,
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,YQ==')
  const {container} = render(<ODSLogo />)
  expect(context).toHaveBeenCalledExactlyOnceWith('2d')
  expect([...pixels]).toEqual([255,255,255,255,255,255,255,0])
  expect(putImageData).toHaveBeenCalledOnce()
  expect(container.firstChild).toHaveAttribute('data-mask-ready')
  expect(container.firstChild.style.getPropertyValue('--ods-logo-mask')).toBe('url("data:image/png;base64,YQ==")')
  expect(container.querySelector('canvas')).toBeNull()
  // Collapsed navigation hides label spans; the decorative mask is not a label.
  expect(container.querySelector('.ods-frosted-mark').tagName).toBe('DIV')
  expect(container.firstChild).toHaveAttribute('aria-hidden','true')
  expect(container.querySelector('img')).toHaveAttribute('alt','')
})

it('ignores a late image load after unmount', () => {
  let image
  vi.stubGlobal('Image', class { constructor() {image=this} })
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
  const {unmount} = render(<ODSLogo />)
  unmount()
  act(() => image.onload())
  expect(context).not.toHaveBeenCalled()
})
