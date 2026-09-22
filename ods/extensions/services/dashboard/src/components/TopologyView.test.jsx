import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { TopologyView } from './TopologyView' // eslint-disable-line no-unused-vars

describe('TopologyView', () => {
  it('renders a fallback identity when a GPU name is missing', () => {
    expect(() => render(<TopologyView topology={{ gpu_count: 1, gpus: [{ index: 0 }], links: [] }} />)).not.toThrow()
    expect(screen.getByText('Unknown GPU')).toBeInTheDocument()
  })
})
