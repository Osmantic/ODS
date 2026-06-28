import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * Tests for the Extensions page rendering of unhealthy/installable derivations
 * (PR #1037 added the unhealthy poller + UI surface). Specifically asserts:
 *   - StatusBadge text for unhealthy
 *   - isToggleable (Extensions.jsx L626) — user-only across enabled/disabled/error/stopped/unhealthy
 *   - showInstall   (Extensions.jsx L628) — not_installed && ext.installable
 *   - Check Logs CTA for unhealthy user extensions
 *
 * Mocks both /api/extensions/catalog and /api/templates because Extensions
 * mounts both fetches in its initial useEffect (lines 162-173); leaving
 * /api/templates unmocked produces an unhandled jsdom rejection.
 */

const makeJsonResponse = (data, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => data,
})

const baseSummary = (overrides = {}) => ({
  total: 1,
  installed: 0,
  stopped: 0,
  unhealthy: 0,
  not_installed: 0,
  installing: 0,
  error: 0,
  incompatible: 0,
  ...overrides,
})

const baseFeature = { category: 'tools', icon: 'Box' }

const installFetchMock = (catalogFixture) => {
  const fetchMock = vi.fn(async (url) => {
    const u = String(url)
    if (u.includes('/api/extensions/catalog')) return makeJsonResponse(catalogFixture)
    if (u.includes('/api/templates')) return makeJsonResponse({ templates: [] })
    throw new Error(`Unmocked fetch: ${u}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Find the per-extension toggle <button> by its uniquely-shaped width class.
// L680 uses Tailwind arbitrary values: `inline-flex h-[18px] w-[32px] ...`
// — the only button on the card with that footprint is the toggle.
const findToggleButton = (container) =>
  Array.from(container.querySelectorAll('button')).find((b) =>
    b.className.includes('w-[32px]')
  )

beforeEach(() => {
  vi.useRealTimers()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Extensions page — unhealthy + install derivations', () => {
  it('renders amber unhealthy badge for unhealthy user ext', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'A user extension whose container is running but failing health checks.',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)

    // Card name shows up only after fetchCatalog resolves.
    await screen.findByText('Unhealthy User Service')

    // StatusBadge L594 renders status.replace(/_/g, ' ') — case is preserved,
    // so 'unhealthy' (lowercase) appears in the DOM. CSS uppercases it visually.
    // Disambiguate from the status legend (L383-392, which also renders keys
    // lowercase) by filtering to the badge's `cursor-help` class.
    const matches = screen.getAllByText('unhealthy')
    const badge = matches.find((el) => el.className.includes('cursor-help'))
    expect(badge).toBeTruthy()
    expect(badge.className).toContain('text-amber-400')
  })

  it('renders toggle switch for unhealthy user ext (isToggleable=true)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // The toggle button is rendered (L676-695) when isToggleable is true.
    await waitFor(() => {
      expect(findToggleButton(container)).toBeTruthy()
    })
  })

  it('renders cli_installed user ext as installed and toggleable', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'aider',
          name: 'Aider',
          status: 'cli_installed',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'CLI-only one-shot extension',
        },
      ],
      summary: baseSummary({ installed: 1, cli_installed: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Aider')

    const matches = screen.getAllByText('cli installed')
    const badge = matches.find((el) => el.className.includes('cursor-help'))
    expect(badge).toBeTruthy()

    const toggle = findToggleButton(container)
    expect(toggle).toBeTruthy()
    expect(toggle.className).toContain('bg-green-500')
    expect(screen.getByText('Disable to remove')).toBeInTheDocument()
  })

  it('does NOT render toggle for unhealthy CORE ext (isToggleable=false because not user)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-core',
          name: 'Unhealthy Core Service',
          status: 'unhealthy',
          source: 'core',
          installable: false,
          features: [baseFeature],
          description: 'A core extension; toggle suppressed regardless of status.',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    const { container } = render(<Extensions />)
    await screen.findByText('Unhealthy Core Service')

    // Core extensions render the "CORE" pill (L665-672) instead of StatusBadge
    // and never get a toggle button — isToggleable requires source === 'user'.
    expect(findToggleButton(container)).toBeUndefined()
  })

  it('does NOT render Install button for unhealthy ext (showInstall=false)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: true, // even installable=true must NOT show Install when status != not_installed
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // showInstall = (status === 'not_installed') && ext.installable  → false here.
    // The Install button (L740-749) renders the literal text " Install".
    // queryByText is exact-by-default; "Installed"/"Installing" labels in the
    // summary bar / status filters won't match.
    expect(screen.queryByText('Install')).toBeNull()
  })

  it('renders Install button for not_installed + installable (showInstall=true)', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-installable',
          name: 'Installable Service',
          status: 'not_installed',
          source: 'user',
          installable: true,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ not_installed: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Installable Service')

    expect(screen.getByText('Install')).toBeInTheDocument()
  })

  it('renders Check Logs CTA for unhealthy user ext', async () => {
    installFetchMock({
      extensions: [
        {
          id: 'svc-unhealthy-user',
          name: 'Unhealthy User Service',
          status: 'unhealthy',
          source: 'user',
          installable: false,
          features: [baseFeature],
          description: 'desc',
        },
      ],
      summary: baseSummary({ unhealthy: 1 }),
      gpu_backend: 'apple',
      agent_available: true,
    })

    render(<Extensions />)
    await screen.findByText('Unhealthy User Service')

    // L760-769: Check Logs button rendered when isUserExt && isUnhealthy.
    expect(screen.getByRole('button', { name: /Check Logs/i })).toBeInTheDocument()
  })
})
