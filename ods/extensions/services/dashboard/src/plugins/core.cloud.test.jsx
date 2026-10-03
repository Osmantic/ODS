import { coreRoutes } from './core'

function findGpuRoute() {
  return coreRoutes.find((r) => r.path === '/gpu')
}

describe('core GPU Monitor route sidebar', () => {
  it('hides sidebar entry while status is loading', () => {
    const route = findGpuRoute()
    expect(route.sidebar({ status: undefined, loading: true })).toBe(false)
  })

  it('hides sidebar entry for cloud inference', () => {
    const route = findGpuRoute()
    const status = { inferenceMode: 'cloud', inferenceSource: 'cloud-mode', gpu: { gpu_count: 2 } }
    expect(route.sidebar({ status, loading: false })).toBe(false)
  })

  it('hides sidebar entry for remote inference', () => {
    const route = findGpuRoute()
    const status = { inferenceMode: 'remote', inferenceSource: 'remote-provider', gpu: { gpu_count: 2 } }
    expect(route.sidebar({ status, loading: false })).toBe(false)
  })

  it('shows sidebar entry for local multi-GPU', () => {
    const route = findGpuRoute()
    const status = { inferenceMode: 'local', inferenceSource: 'local-runtime', gpu: { gpu_count: 2 } }
    expect(route.sidebar({ status, loading: false })).toBe(true)
  })

  it('hides sidebar entry for local single-GPU', () => {
    const route = findGpuRoute()
    const status = { inferenceMode: 'local', inferenceSource: 'local-runtime', gpu: { gpu_count: 1 } }
    expect(route.sidebar({ status, loading: false })).toBe(false)
  })

  it('getProps passes status and loading', () => {
    const route = findGpuRoute()
    const status = { inferenceMode: 'local' }
    expect(route.getProps({ status, loading: true })).toEqual({ status, loading: true })
  })
})
