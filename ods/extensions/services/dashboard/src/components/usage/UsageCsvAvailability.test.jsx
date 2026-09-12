import { fireEvent, render, screen } from '@testing-library/react'
import UsageView from './UsageView'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

test('exports unavailable counters and unknown costs as empty cells, preserving measured zero', async () => {
  let artifact
  vi.stubGlobal('URL', { createObjectURL: vi.fn(blob => { artifact = blob; return 'blob:test' }), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const models = [
    { model: 'Unknown', requests: 0, cost_usd: 0, cost_source: 'untracked', input_tokens: 30 },
    { model: 'Local', requests: 0, cost_usd: 0, cost_source: 'local_zero_cost', input_tokens: 20 },
    { model: 'Billed', requests: 2, cost_usd: 0, cost_source: 'actual_billed', input_tokens: 10 },
  ]
  render(<UsageView report={{ source: { status: 'ok', local_runtime: { request_count_available: false } }, summary: {}, models }}
    readiness={{ status: 'ready' }} range={{ start: '2026-09-01' }} />)
  fireEvent.click(screen.getByRole('button', { name: 'Models', exact: true }))
  fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
  const csv = await new Promise((resolve, reject) => {
    const reader = new globalThis.FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsText(artifact)
  })
  const rows = csv.split('\r\n').slice(1).map(row => row.split(',').map(cell => cell.slice(1, -1)))
  expect(rows.map(row => [row[0], row[7], row[8], row[9]])).toEqual([
    ['Unknown', '', '', 'untracked'], ['Local', '', '0', 'local_zero_cost'], ['Billed', '2', '0', 'actual_billed'],
  ])
})
