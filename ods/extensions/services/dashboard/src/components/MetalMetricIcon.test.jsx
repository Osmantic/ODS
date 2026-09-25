import {render,cleanup} from '@testing-library/react'
import {afterEach,it,expect,vi} from 'vitest'
import {Cpu} from 'lucide-react'
import MetalMetricIcon from './MetalMetricIcon'
afterEach(() => {cleanup();vi.unstubAllGlobals()})
it('keeps icon paths and gives each metal gradient a unique reference', () => {
  vi.stubGlobal('matchMedia',() => ({matches:false,addEventListener(){},removeEventListener(){}}))
  const {container} = render(<><MetalMetricIcon icon={Cpu}/><MetalMetricIcon icon={Cpu}/></>)
  const icons = [...container.querySelectorAll('svg')]
  expect(icons).toHaveLength(2)
  expect(icons[0].querySelector('path')).not.toBeNull()
  expect(icons[0].getAttribute('stroke')).not.toBe(icons[1].getAttribute('stroke'))
  for (const icon of icons) {
    expect(icon.getAttribute('stroke')).toBe(`url(#${icon.querySelector('linearGradient').id})`)
    expect(icon.querySelector('animateTransform')).not.toBeNull()
  }
})
it('keeps the silver treatment static with reduced motion', () => {
  vi.stubGlobal('matchMedia',() => ({matches:true,addEventListener(){},removeEventListener(){}}))
  const {container} = render(<MetalMetricIcon icon={Cpu}/>)
  expect(container.querySelector('linearGradient')).not.toBeNull()
  expect(container.querySelector('animateTransform')).toBeNull()
})
