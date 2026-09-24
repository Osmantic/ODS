import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { render } from '../test/test-utils'
import { TopologyView } from './TopologyView' // eslint-disable-line no-unused-vars

const topology = {
  gpu_count: 3,
  vendor: 'nvidia',
  driver_version: '600.1',
  mig_enabled: false,
  gpus: [
    { index: 0, name: 'NVIDIA Alpha', memory_gb: 24 },
    { index: 1, name: 'NVIDIA Beta', memory_gb: 24 },
    { index: 2, name: 'NVIDIA Gamma', memory_gb: 48 },
  ],
  links: [
    { gpu_a: 0, gpu_b: 1, link_type: 'NVLink', rank: 100 },
    { gpu_a: 0, gpu_b: 2, link_type: 'PHB', rank: 20 },
    { gpu_a: 1, gpu_b: 2, link_type: 'SYS', rank: 5 },
  ],
}

describe('TopologyView GPU focus', () => {
  it('focuses direct links from a GPU chip and toggles back to the full matrix', () => {
    render(<TopologyView topology={topology} />)
    const focusButton = screen.getByRole('button', { name: 'Focus GPU 1' })
    const directCell = screen.getAllByTitle(/GPU0.*GPU1/)[0].closest('td')
    const unrelatedCell = screen.getAllByTitle(/GPU0.*GPU2/)[0].closest('td')

    fireEvent.click(focusButton)

    expect(focusButton).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Focused on GPU1')
    expect(directCell).not.toHaveClass('opacity-20')
    expect(unrelatedCell).toHaveClass('opacity-20')

    fireEvent.click(focusButton)

    expect(focusButton).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(unrelatedCell).not.toHaveClass('opacity-20')
  })

  it('supports keyboard focus controls through native buttons', async () => {
    const user = userEvent.setup()
    render(<TopologyView topology={topology} />)
    const focusButton = screen.getByRole('button', { name: 'Focus GPU 2' })

    focusButton.focus()
    await user.keyboard('{Enter}')

    expect(focusButton).toHaveFocus()
    expect(focusButton).toHaveAttribute('aria-pressed', 'true')
  })
})
