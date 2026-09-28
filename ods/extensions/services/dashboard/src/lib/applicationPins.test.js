import { APPLICATIONS_CHANGED, isPinned, readUnpinned, setPinned } from './applicationPins'

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.restoreAllMocks() })

test('extensions are pinned by default and unpinning is remembered', () => {
  const changed = vi.fn()
  window.addEventListener(APPLICATIONS_CHANGED, changed)
  expect(isPinned('uptime-kuma')).toBe(true)
  setPinned('uptime-kuma', false)
  setPinned('uptime-kuma', false)
  expect(readUnpinned()).toEqual(['uptime-kuma'])
  expect(JSON.parse(localStorage.getItem('ods.applications.unpinned.v1'))).toEqual(['uptime-kuma'])
  setPinned('uptime-kuma', true)
  expect(isPinned('uptime-kuma')).toBe(true)
  expect(changed).toHaveBeenCalledTimes(3)
  window.removeEventListener(APPLICATIONS_CHANGED, changed)
})

test('ignores invalid identifiers and unreadable stored values', () => {
  setPinned('../evil', false)
  expect(readUnpinned()).toEqual([])
  localStorage.setItem('ods.applications.unpinned.v1', '{not json')
  expect(readUnpinned()).toEqual([])
  localStorage.setItem('ods.applications.unpinned.v1', JSON.stringify(['kroki', 42, 'Bad Id', 'kroki']))
  expect(readUnpinned()).toEqual(['kroki'])
})

test('keeps working for this page when browser storage is unavailable', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  setPinned('cyberchef', false)
  expect(isPinned('cyberchef')).toBe(false)
  setPinned('cyberchef', true)
  expect(isPinned('cyberchef')).toBe(true)
})
