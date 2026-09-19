import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import UsageView from './UsageView'

const report = {
  source: {status: 'ok', local_runtime: {request_count_available: false}},
  summary: {},
  daily: [
    {date: '2026-01-01', input_tokens: 0, output_tokens: 12, cache_read_tokens: 3, cache_write_tokens: 4, requests: 0},
    {date: '2026-01-02', input_tokens: 24, output_tokens: null, requests: 2},
    {date: '2999-01-01', input_tokens: 100},
  ],
}

function show(overrides = {}) {
  return render(<UsageView report={report} range={{start: '2026-01-01', end: '2026-01-31'}} readiness={{status: 'ready'}} loading={false} {...overrides}/> )
}

describe('daily usage download at the activity view boundary', () => {
  let click
  beforeEach(() => {
    vi.stubGlobal('URL', Object.assign(URL, {createObjectURL: vi.fn(() => 'blob:daily-usage'), revokeObjectURL: vi.fn()}))
    click = vi.spyOn(window.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('exports exact UTC counters, distinguishes unknown requests from zero, and excludes future days', async () => {
    show()
    fireEvent.click(screen.getByRole('button', {name: 'Export daily CSV'}))
    const blob = URL.createObjectURL.mock.calls[0][0]
    const csv = await new Promise(resolve => { const reader = new window.FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob) })
    expect(csv).toBe('date,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,requests\r\n2026-01-01,0,12,3,4,\r\n2026-01-02,24,,,,2')
    expect(click.mock.instances[0].download).toBe('ods-daily-usage-2026-01-01-to-2026-01-02.csv')
    expect(click.mock.instances[0].isConnected).toBe(false)
    // The blob URL must outlive the click handoff: revoking it synchronously
    // races the browser's download navigation (issue #5113).
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:daily-usage'), {timeout: 2000})
    expect(click.mock.invocationCallOrder[0]).toBeLessThan(URL.revokeObjectURL.mock.invocationCallOrder[0])
  })

  it.each([{loading: true}, {error: 'offline'}, {report: {...report, source: {status: 'unavailable'}}}, {report: {...report, daily: []}}])('disables unavailable or empty snapshots: %j', overrides => {
    show(overrides)
    expect(screen.getByRole('button', {name: 'Export daily CSV'})).toBeDisabled()
  })

  it('reports a failed browser download, cleans up, and permits an explicit retry', async () => {
    click.mockImplementationOnce(() => { throw new Error('Downloads blocked') })
    show()
    fireEvent.click(screen.getByRole('button', {name: 'Export daily CSV'}))
    expect(screen.getByRole('alert')).toHaveTextContent('Downloads blocked')
    expect(document.querySelector('a[download]')).toBeNull()
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:daily-usage'), {timeout: 2000})
    fireEvent.click(screen.getByRole('button', {name: 'Export daily CSV'}))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(click).toHaveBeenCalledTimes(2)
  })
})
