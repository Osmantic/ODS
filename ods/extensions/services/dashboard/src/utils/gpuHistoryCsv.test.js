import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadGpuHistoryCsv, gpuHistoryToCsv } from './gpuHistoryCsv'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('gpuHistoryToCsv', () => {
  it('exports aligned long-form rows in timestamp and numeric GPU order', () => {
    const csv = gpuHistoryToCsv({
      timestamps: ['2026-09-04T00:00:00Z', '2026-09-04T00:00:05Z'],
      gpus: {
        10: {
          utilization: [90, 91],
          memory_percent: [80, 81],
          temperature: [70, 71],
          power_w: [null, 301],
        },
        2: {
          utilization: [20, 21],
          memory_percent: [30, 31],
          temperature: [40, 41],
          power_w: [50, 51],
        },
      },
    })

    expect(csv).toBe([
      'timestamp,gpu_index,utilization_percent,memory_percent,temperature_c,power_w',
      '2026-09-04T00:00:00Z,2,20,30,40,50',
      '2026-09-04T00:00:00Z,10,90,80,70,',
      '2026-09-04T00:00:05Z,2,21,31,41,51',
      '2026-09-04T00:00:05Z,10,91,81,71,301',
      '',
    ].join('\r\n'))
  })

  it('keeps unavailable metrics blank and neutralizes spreadsheet formulas', () => {
    const csv = gpuHistoryToCsv({
      timestamps: ['=unsafe'],
      gpus: {
        '@gpu,0': { utilization: [null] },
      },
    })

    expect(csv).toContain("'=unsafe,\"'@gpu,0\",,,,")
  })

  it('preserves numeric temperatures and represents nonfinite telemetry as a gap', () => {
    expect(gpuHistoryToCsv({ timestamps: ['sample'], gpus: { 0: {
      utilization: [NaN], temperature: [-5], power_w: [Infinity],
    } } })).toContain('sample,0,,,-5,\r\n')
  })

  it('keeps the generated download URL alive until the browser has consumed the click', () => {
    const create = vi.fn(() => 'blob:gpu-history')
    const revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    vi.spyOn(window.HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      expect(this.isConnected).toBe(true)
      expect(this.download).toBe('ods-gpu-history-2026-09-16T00-00-00Z.csv')
      expect(revoke).not.toHaveBeenCalled()
    })
    let release
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(callback => { release = callback; return 1 })
    downloadGpuHistoryCsv({ timestamps: ['sample'], gpus: { 0: {} } }, new Date('2026-09-16T00:00:00Z'))
    expect(create.mock.calls[0][0].type).toBe('text/csv;charset=utf-8')
    expect(document.querySelector('a[download]')).toBeNull()
    expect(revoke).not.toHaveBeenCalled()
    release()
    expect(revoke).toHaveBeenCalledWith('blob:gpu-history')
  })
})
