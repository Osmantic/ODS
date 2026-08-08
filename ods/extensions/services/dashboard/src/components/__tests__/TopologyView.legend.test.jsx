import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
// eslint-disable-next-line no-unused-vars -- TopologyView is used via JSX below
import { TopologyView, linkStyle, TOPOLOGY_LEGEND } from '../TopologyView'

// The matrix colours each interconnect cell with linkStyle(link.rank). The
// legend must therefore label each swatch with the colour that rank produces,
// or it mis-describes the grid it sits under.
//
// Ranks are the values installers/lib/{nvidia,amd}-topo.sh actually emit.
const REAL_RANKS = {
  NVLink: 100, // NV4/6/8/12/18
  XGMI: 90,
  PIX: 50, // same PCIe switch
  PXB: 40, // cross switch
  PHB: 30, // host bridge
  SYS: 10, // cross-NUMA
}

describe('TopologyView legend', () => {
  it('colours each legend entry with its real emitted rank', () => {
    for (const { label, rank } of TOPOLOGY_LEGEND) {
      expect(rank).toBe(REAL_RANKS[label])
    }
  })

  it('does not claim PIX is a high-bandwidth (indigo) link', () => {
    // Regression: the legend hard-coded PIX at rank 60 -> indigo, but a real
    // PIX link (rank 50) renders yellow in the matrix.
    const pix = TOPOLOGY_LEGEND.find(e => e.label === 'PIX')
    expect(pix.rank).toBe(REAL_RANKS.PIX)
    expect(linkStyle(pix.rank).dot).toBe('bg-yellow-400')
    expect(linkStyle(pix.rank).dot).not.toBe('bg-indigo-400')
  })

  it('renders the legend swatch in the same colour the matrix uses for that link', () => {
    const topology = {
      vendor: 'nvidia',
      gpu_count: 2,
      gpus: [
        { index: 0, name: 'NVIDIA A', memory_gb: 24 },
        { index: 1, name: 'NVIDIA B', memory_gb: 24 },
      ],
      // A PIX interconnect between the two GPUs.
      links: [{ gpu_a: 0, gpu_b: 1, rank: REAL_RANKS.PIX, link_type: 'PIX', link_label: 'PCIe-SameSwitch' }],
    }
    const { container } = render(<TopologyView topology={topology} />)

    // The matrix cell for the PIX link (symmetric grid renders it twice).
    const matrixCell = screen.getAllByTitle('GPU0 ↔ GPU1: PIX')[0]
    expect([...matrixCell.classList]).toContain(linkStyle(REAL_RANKS.PIX).text)

    // The legend PIX swatch dot must carry the same colour family.
    const legend = container.querySelector('.border-t')
    const pixEntry = within(legend).getByText('PIX')
    const pixDot = pixEntry.querySelector('span')
    expect([...pixDot.classList]).toContain(linkStyle(REAL_RANKS.PIX).dot)
  })
})
