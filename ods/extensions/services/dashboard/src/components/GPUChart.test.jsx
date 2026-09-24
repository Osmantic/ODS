import { render } from '@testing-library/react'
import { GPUChart } from './GPUChart'

test('keeps unavailable GPU samples as gaps at their real timestamps', () => {
  const history = { timestamps: [0, 5000, 10000, 30000, 35000].map(time => new Date(time).toISOString()),
    gpus: { 0: { utilization: [10, 20, null, 30, 40] } } }
  const { container } = render(<GPUChart history={history} gpuIndex={0} />)
  const graph = container.querySelector('svg')
  const segments = graph.querySelectorAll('polyline')
  expect(segments).toHaveLength(2)
  expect(segments[0].getAttribute('points').split(' ').map(point => point.split(',')[0])).toEqual(['0.0', '42.9'])
  expect(segments[1].getAttribute('points').split(' ').map(point => point.split(',')[0])).toEqual(['257.1', '300.0'])
})

test('shows an isolated valid sample without stretching it across missing samples', () => {
  const history = { timestamps: [0, 5000, 10000].map(time => new Date(time).toISOString()),
    gpus: { 0: { utilization: [null, 0, null] } } }
  const { container } = render(<GPUChart history={history} gpuIndex={0} />)
  expect(container.querySelectorAll('polyline')).toHaveLength(0)
  expect(container.querySelector('circle')).toHaveAttribute('cx', '150.0')
})
