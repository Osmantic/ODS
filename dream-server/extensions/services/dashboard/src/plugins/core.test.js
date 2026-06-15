import { coreRoutes } from './core'


describe('core GPU Monitor route', () => {
  const route = coreRoutes.find(item => item.id === 'gpu-monitor')

  test('is discoverable for single-GPU AMD provider diagnostics', () => {
    expect(route.sidebar({ status: { gpu: { gpu_count: 1, backend: 'amd' } } })).toBe(true)
  })

  test('stays hidden for single-GPU non-AMD systems', () => {
    expect(route.sidebar({ status: { gpu: { gpu_count: 1, backend: 'nvidia' } } })).toBe(false)
  })
})
