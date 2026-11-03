import { getSidebarNavItems } from './registry'

const ids = status => getSidebarNavItems({ status, loading: false }).map(item => item.id)

it('hides pages that cannot work on Windows Desktop', () => {
  const desktop = ids({ runtimeProfile: 'windows-desktop', gpu: { gpu_count: 2 } })
  expect(desktop).toEqual(expect.arrayContaining(['dashboard', 'settings']))
  expect(desktop).not.toEqual(expect.arrayContaining(['extensions']))
  expect(desktop).not.toContain('models')
  expect(desktop).not.toContain('gpu-monitor')
})

it('keeps every entry for the Compose stack', () => {
  expect(ids({ runtimeProfile: 'compose', gpu: { gpu_count: 2 } })).toEqual(
    expect.arrayContaining(['dashboard', 'extensions', 'models', 'gpu-monitor', 'settings']))
})
