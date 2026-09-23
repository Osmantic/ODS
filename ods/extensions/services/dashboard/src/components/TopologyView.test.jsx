import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { TopologyView } from './TopologyView' // eslint-disable-line no-unused-vars

describe('TopologyView', () => {
  it('rejects an oversized GPU count before allocating a matrix', () => {
    expect(() => render(<TopologyView topology={{ gpu_count: 100000, gpus: [], links: [] }} />)).not.toThrow()
    expect(screen.getByRole('alert')).toHaveTextContent('exceeds the supported GPU limit')
    expect(screen.queryByText('GPU Interconnect Topology')).not.toBeInTheDocument()
  })
})
