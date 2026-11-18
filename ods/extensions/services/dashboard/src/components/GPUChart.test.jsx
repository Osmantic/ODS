import {render, screen} from '@testing-library/react'
import {GPUChart} from './GPUChart'

function show(values, seconds = values.map((_, index) => index * 5)) {
  render(<GPUChart gpuIndex={0} history={{timestamps:seconds.map(value => new Date(value * 1000).toISOString()),gpus:{'0':{utilization:values}}}}/>)
  return screen.getByText('Utilization %').parentElement.parentElement
}

it('keeps missing telemetry as gaps and places retained samples at their actual times', () => {
  const metric=show([10,20,null,30,40],[0,5,10,30,35])
  const segments=metric.querySelectorAll('polyline')
  expect(segments).toHaveLength(2)
  expect(segments[0]).toHaveAttribute('points','0.0,26.0 42.9,18.0')
  expect(segments[1]).toHaveAttribute('points','257.1,10.0 300.0,2.0')
})

it('shows an isolated measured zero without connecting across unavailable samples', () => {
  const metric=show([null,0,null])
  expect(metric.querySelectorAll('polyline')).toHaveLength(0)
  expect(metric.querySelector('circle')).toHaveAttribute('cx','150.0')
  expect(metric.querySelector('circle')).toHaveAttribute('cy','34.0')
})

it('retains the empty placeholder when no measurement is available', () => {
  const metric=show([null,null,null])
  expect(metric.querySelector('svg')).toBeNull()
})

it('keeps every measured point when no samples are missing', () => {
  const metric=show([0,50,100])
  expect(metric.querySelectorAll('polyline')).toHaveLength(1)
  expect(metric.querySelector('polyline')).toHaveAttribute('points','0.0,34.0 150.0,18.0 300.0,2.0')
})
