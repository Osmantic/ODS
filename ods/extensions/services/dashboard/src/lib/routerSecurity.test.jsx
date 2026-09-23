import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { BrowserRouter, useNavigate } from 'react-router-dom'
import { Link } from 'react-router-dom'

// A path object denotes an internal route. Leading slash/backslash combinations
// must not become a protocol-relative external URL (GHSA-wrjc-x8rr-h8h6).
const ambiguousPaths = [
  '//outside.example/settings',
  '/\\outside.example/settings',
  '\\\\outside.example/settings',
]

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('router navigation security', () => {
  it.each(ambiguousPaths)('keeps Link path %j on the dashboard origin', (pathname) => {
    render(<BrowserRouter><Link to={{ pathname }}>Open panel</Link></BrowserRouter>)
    const link = screen.getByRole('link', { name: 'Open panel' })
    const destination = new URL(link.href)
    expect(destination.origin).toBe(window.location.origin)
    expect(destination.pathname).toBe('/outside.example/settings')
  })

  it.each(ambiguousPaths)('rejects useNavigate path %j that a browser interprets as external', (pathname) => {
    const before = window.location.href
    const { result } = renderHook(() => useNavigate(), { wrapper: BrowserRouter })
    expect(() => act(() => result.current({ pathname }))).toThrow('External navigation is not allowed')
    expect(window.location.href).toBe(before)
  })
})
