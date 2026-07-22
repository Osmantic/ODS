import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { GPUCard } from './GPUCard' // eslint-disable-line no-unused-vars

describe('GPUCard sensor availability', () => {
  it('does not present unavailable Windows counters as zero', () => {
    render(<GPUCard gpu={{
      index: 0,
      uuid: 'amd-windows-host-0',
      name: 'AMD Radeon RX 9070 XT',
      memory_used_mb: 0,
      memory_total_mb: 0,
      memory_percent: 0,
      utilization_percent: 0,
      temperature_c: 0,
      power_w: null,
      assigned_services: ['llama-server'],
      memory_usage_available: false,
      utilization_available: false,
      temperature_available: false,
    }} />)

    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
    expect(screen.queryByText('0°C')).not.toBeInTheDocument()
    expect(screen.getByText('llama-server')).toBeInTheDocument()
  })

  it('renders real counters when the host agent reports them', () => {
    render(<GPUCard gpu={{
      index: 0,
      uuid: 'amd-windows-host-0',
      name: 'AMD Radeon RX 9070 XT',
      memory_used_mb: 4096,
      memory_total_mb: 16384,
      memory_percent: 25,
      utilization_percent: 42,
      temperature_c: 0,
      power_w: null,
      assigned_services: [],
      memory_usage_available: true,
      utilization_available: true,
      temperature_available: false,
    }} />)

    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('4.0/16 GB')).toBeInTheDocument()
    expect(screen.queryByText('0°C')).not.toBeInTheDocument()
  })
})
