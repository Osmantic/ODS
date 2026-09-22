import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { TopologyView } from './TopologyView'

describe('TopologyView', () => {
  it('ignores links outside the reported GPU range', () => {
    render(<TopologyView topology={{
      gpu_count: 2,
      gpus: [
        { index: 0, name: 'NVIDIA A', memory_gb: 16 },
        { index: 1, name: 'NVIDIA B', memory_gb: 16 },
      ],
      links: [
        { gpu_a: 0, gpu_b: 4, link_type: 'NVLink', rank: 100 },
        { gpu_a: -1, gpu_b: 1, link_type: 'SYS', rank: 5 },
        { gpu_a: 0, gpu_b: 1, link_type: 'PIX', rank: 60 },
      ],
      vendor: 'nvidia',
    }} />)

    expect(screen.getAllByText('PIX').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByTitle('GPU0 ↔ GPU4: NVLink')).toBeNull()
  })
})
