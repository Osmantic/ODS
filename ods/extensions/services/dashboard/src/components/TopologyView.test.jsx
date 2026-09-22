import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { TopologyView } from './TopologyView' // eslint-disable-line no-unused-vars

describe('TopologyView', () => {
  it('ignores links whose endpoints are outside the reported GPU range', () => {
    expect(() => render(<TopologyView topology={{
      gpu_count: 2,
      gpus: [
        { index: 0, name: 'NVIDIA A', memory_gb: 8 },
        { index: 1, name: 'NVIDIA B', memory_gb: 8 },
      ],
      links: [
        { gpu_a: 0, gpu_b: 1, link_type: 'NVLink', rank: 100 },
        { gpu_a: 1, gpu_b: 9, link_type: 'SYS', rank: 5 },
        { gpu_a: -1, gpu_b: 0, link_type: 'SYS', rank: 5 },
      ],
      vendor: 'NVIDIA',
    }} />)).not.toThrow()

    expect(screen.getByRole('alert')).toHaveTextContent('Some invalid topology links were ignored.')
    expect(screen.getAllByText('NVLink').length).toBeGreaterThan(0)
    expect(screen.queryByTitle(/GPU1 ↔ GPU9/)).not.toBeInTheDocument()
  })
})
