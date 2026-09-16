import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, fireEvent } from '@testing-library/react'
import ODSLogo from './ODSLogo'

vi.mock('@paper-design/shaders-react', () => ({ LiquidMetal: ({speed}) => <canvas data-speed={speed}/> }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('keeps the supplied logo visible when WebGL is unavailable', () => {
  const removeEventListener = vi.fn()
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  const { container, unmount } = render(<ODSLogo />)
  expect(container.querySelector('img')).toHaveAttribute('src', '/osmantic-isolated-os.png')
  expect(container.querySelector('img')).toHaveStyle({filter:'grayscale(1)'})
  expect(container.querySelector('canvas')).toBeNull()
  unmount()
  expect(removeEventListener).toHaveBeenCalled()
})

it('keeps the dashboard usable when a browser denies GPU contexts', () => {
  vi.stubGlobal('matchMedia', () => ({matches:false, addEventListener:vi.fn(), removeEventListener:vi.fn()}))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => { throw new Error('GPU disabled') })
  const {container} = render(<ODSLogo />)
  expect(container.querySelector('img')).toHaveAttribute('src', '/osmantic-isolated-os.png')
  expect(container.querySelector('canvas')).toBeNull()
})

it('falls back to the static logo when canvas readback is denied', () => {
  vi.stubGlobal('matchMedia', () => ({matches:false, addEventListener:vi.fn(), removeEventListener:vi.fn()}))
  vi.stubGlobal('Image', class {
    naturalWidth = 1
    naturalHeight = 1
    set src(_value) { this.onload() }
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(type => type === 'webgl2'
    ? {getExtension:() => null}
    : {drawImage:vi.fn(), getImageData:() => { throw new Error('Canvas readback denied') }})
  const {container} = render(<ODSLogo />)
  expect(container.querySelector('img')).toHaveAttribute('src', '/osmantic-isolated-os.png')
  expect(container.querySelector('canvas')).toBeNull()
})

it.each([false,true])('animates only on hover and honors reduced motion: %s', reduced => {
  vi.stubGlobal('matchMedia', () => ({matches:reduced}))
  vi.stubGlobal('Image', class { naturalWidth=1; naturalHeight=1; set src(_value) {this.onload()} })
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(type => type === 'webgl2' ? {getExtension:()=>null} : {drawImage:vi.fn(),getImageData:()=>({data:new Uint8ClampedArray([255,0,0,255])}),putImageData:vi.fn()})
  vi.spyOn(HTMLCanvasElement.prototype,'toDataURL').mockReturnValue('data:image/png;base64,YQ==')
  const {container}=render(<ODSLogo active/>)
  expect(container.querySelector('canvas')).toHaveAttribute('data-speed','0')
  fireEvent.mouseEnter(container.firstChild)
  expect(container.querySelector('canvas')).toHaveAttribute('data-speed',reduced ? '0' : '0.35')
  fireEvent.mouseLeave(container.firstChild)
  expect(container.querySelector('canvas')).toHaveAttribute('data-speed','0')
})
