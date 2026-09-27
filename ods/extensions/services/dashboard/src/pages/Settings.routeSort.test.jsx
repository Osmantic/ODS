import { describe, test, expect } from 'vitest'
import { sortRoutesBySeverity } from './Settings'

describe('sortRoutesBySeverity', () => {
  test('orders by severity: down, unhealthy, degraded, unknown, healthy', () => {
    const items = [
      { id: 'a', status: 'healthy' },
      { id: 'b', status: 'down' },
      { id: 'c', status: 'unknown' },
      { id: 'd', status: 'degraded' },
      { id: 'e', status: 'unhealthy' },
    ]
    expect(sortRoutesBySeverity(items).map(i => i.id)).toEqual(['b', 'e', 'd', 'c', 'a'])
  })

  test('places an unrecognized status after all known statuses', () => {
    const items = [
      { id: 'a', status: 'healthy' },
      { id: 'b', status: 'some-future-status' },
      { id: 'c', status: 'down' },
    ]
    expect(sortRoutesBySeverity(items).map(i => i.id)).toEqual(['c', 'a', 'b'])
  })

  test('does not mutate the input array', () => {
    const items = [{ id: 'a', status: 'healthy' }, { id: 'b', status: 'down' }]
    const original = [...items]
    sortRoutesBySeverity(items)
    expect(items).toEqual(original)
  })

  test('handles null/undefined input without throwing', () => {
    expect(sortRoutesBySeverity(null)).toEqual([])
    expect(sortRoutesBySeverity(undefined)).toEqual([])
  })

  test('handles an empty array', () => {
    expect(sortRoutesBySeverity([])).toEqual([])
  })
})
