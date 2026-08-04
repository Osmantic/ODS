import { describe, expect, it } from 'vitest'
import { formatUptime } from '../formatUptime'

describe('formatUptime', () => {
  it('renders days once the uptime passes 24h', () => {
    // Dropping the day unit turned three days into "83h 20m" on Settings.
    expect(formatUptime(3 * 86400 + 11 * 3600 + 20 * 60)).toBe('3d 11h 20m')
    expect(formatUptime(90061)).toBe('1d 1h 1m')
  })

  it('omits the day unit below 24h', () => {
    expect(formatUptime(3600 + 5 * 60)).toBe('1h 5m')
  })

  it('omits the hour unit below 1h', () => {
    expect(formatUptime(7 * 60)).toBe('7m')
    expect(formatUptime(59)).toBe('0m')
  })

  it('renders absent values as an em dash', () => {
    expect(formatUptime(0)).toBe('—')
    expect(formatUptime(undefined)).toBe('—')
    expect(formatUptime(null)).toBe('—')
  })
})
