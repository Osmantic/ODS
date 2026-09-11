import {render, cleanup} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import mascotSource from '../../public/pixel-mascot.js?raw'
import vm from 'node:vm'
import PixelMascot from './PixelMascot'
import {pixelHeaderPose, pixelReplyPose} from '../lib/pixelMascotState'

afterEach(() => {cleanup(); vi.unstubAllGlobals()})

it('updates state without remounting the SVG and destroys the renderer on unmount', () => {
  const renderer = {mount:vi.fn(), setState:vi.fn(), destroy:vi.fn()}
  vi.stubGlobal('PixelMascot', renderer)
  const view = render(<PixelMascot state="thinking"/>)
  const element = view.container.firstChild
  view.rerender(<PixelMascot state="done" settled/>)
  expect(renderer.mount).toHaveBeenCalledTimes(1)
  expect(renderer.setState).toHaveBeenLastCalledWith(element,'done',{settled:true})
  expect(renderer.destroy).not.toHaveBeenCalled()
  view.unmount()
  expect(renderer.destroy).toHaveBeenCalledWith(element)
})

it('maps waiting, actual work, errors and historical replies coherently', () => {
  expect(pixelHeaderPose({status:'available'})).toBe('idle')
  expect(pixelHeaderPose({status:'available',sending:true})).toBe('thinking')
  expect(pixelHeaderPose({status:'available',stopping:true,sending:true})).toBe('waiting')
  expect(pixelHeaderPose({status:'available',restoredActive:true})).toBe('working')
  expect(pixelHeaderPose({status:'unavailable'})).toBe('blocked')
  expect(pixelHeaderPose({status:'available',interrupted:true,restoredActivity:'unknown'})).toBe('blocked')
  expect(pixelReplyPose({status:'streaming'},false)).toBe('waiting')
  expect(pixelReplyPose({status:'streaming'},true)).toBe('thinking')
  expect(pixelReplyPose({content:'Done'})).toBe('done')
  expect(pixelReplyPose({content:'Error',task:{state:'failed'}})).toBe('blocked')
  expect(pixelReplyPose({status:'stopped'})).toBe('idle')
})

function loadRenderer(reduced = false) {
  let frame
  const runtime = {document, performance:{now:() => 0}, requestAnimationFrame:vi.fn(callback => {frame=callback; return 1}), cancelAnimationFrame:vi.fn(), matchMedia:() => ({matches:reduced,addEventListener(){}})}
  vm.runInNewContext(mascotSource,runtime)
  return {renderer:runtime.PixelMascot, runtime, advance:time => frame?.(time)}
}

it('the original renderer moves a live pose, keeps settled replies still and honors reduced motion', () => {
  const {renderer,runtime,advance} = loadRenderer()
  const element = document.createElement('span')
  document.body.append(element)
  renderer.mount(element,{state:'thinking'})
  const motion = element.querySelector('svg > g')
  const initial = motion.getAttribute('transform')
  advance(1000)
  expect(motion.getAttribute('transform')).not.toBe(initial)
  expect(runtime.requestAnimationFrame).toHaveBeenCalled()
  renderer.setState(element,'done',{settled:true})
  expect(element.dataset.mascotState).toBe('done')
  const still = motion.getAttribute('transform')
  advance(3000)
  advance(6000)
  expect(motion.getAttribute('transform')).toBe(still)
  renderer.destroy(element)
  element.remove()
  const reduced = loadRenderer(true)
  const quiet = document.createElement('span')
  document.body.append(quiet)
  reduced.renderer.mount(quiet,{state:'thinking'})
  expect(reduced.runtime.requestAnimationFrame).not.toHaveBeenCalled()
  expect(reduced.renderer.samplePose('thinking',0,{reduced:true})).toEqual(reduced.renderer.samplePose('thinking',10,{reduced:true}))
  reduced.renderer.destroy(quiet)
  quiet.remove()
})
