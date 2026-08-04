import { act, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { ThemeProvider, useTheme } from './ThemeContext'

function ThemeProbe() {
  const { theme, cycleTheme } = useTheme()
  return createElement('button', { onClick: cycleTheme }, theme)
}

afterEach(() => {
  vi.restoreAllMocks()
  globalThis.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('ThemeProvider storage resilience', () => {
  test('uses the default theme when browser storage is unavailable', () => {
    vi.spyOn(globalThis.Storage.prototype, 'getItem').mockImplementation(() => {
      throw new globalThis.DOMException('blocked', 'SecurityError')
    })
    vi.spyOn(globalThis.Storage.prototype, 'setItem').mockImplementation(() => {
      throw new globalThis.DOMException('blocked', 'SecurityError')
    })

    render(createElement(ThemeProvider, null, createElement(ThemeProbe)))

    expect(screen.getByRole('button')).toHaveTextContent('ods')
    expect(document.documentElement).toHaveAttribute('data-theme', 'ods')
  })

  test('continues changing themes when persistence is denied', () => {
    vi.spyOn(globalThis.Storage.prototype, 'setItem').mockImplementation(() => {
      throw new globalThis.DOMException('quota unavailable', 'QuotaExceededError')
    })

    render(createElement(ThemeProvider, null, createElement(ThemeProbe)))
    act(() => screen.getByRole('button').click())

    expect(screen.getByRole('button')).toHaveTextContent('lemonade')
    expect(document.documentElement).toHaveAttribute('data-theme', 'lemonade')
  })
})
