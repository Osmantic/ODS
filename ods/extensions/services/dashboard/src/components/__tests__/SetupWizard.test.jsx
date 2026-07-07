import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// Mock children that do their own network I/O so the wizard can be navigated
// without standing up the entire dashboard backend.
vi.mock('../PreFlightChecks', () => ({
  PreFlightChecks: ({ onComplete: _onComplete, onIssuesFound: _onIssuesFound }) =>
    <div data-testid="preflight-stub">preflight</div>
}))

vi.mock('../TemplatePicker', () => ({
  TemplatePicker: () => <div data-testid="template-picker-stub">templates</div>
}))

vi.mock('../../lib/templates', () => ({
  getTemplateStatus: () => 'available'
}))

import SetupWizard from '../SetupWizard' // eslint-disable-line no-unused-vars

/**
 * Build a minimal Response-shaped object whose body.getReader() yields the
 * supplied text chunks one at a time. The SetupWizard.runDiagnostics loop
 * only ever uses res.body.getReader().read() / .releaseLock(), so we don't
 * need a full Web Streams implementation here.
 */
function mockStreamResponse(chunks) {
  let idx = 0
  const reader = {
    read: () => {
      if (idx >= chunks.length) {
        return Promise.resolve({ done: true, value: undefined })
      }
      const value = new TextEncoder().encode(chunks[idx++])
      return Promise.resolve({ done: false, value })
    },
    releaseLock: () => {}
  }
  return {
    body: { getReader: () => reader }
  }
}

/**
 * Build a Response whose body.getReader().read() never resolves until the
 * supplied AbortSignal aborts, at which point read() rejects with AbortError.
 * Used to exercise the AbortController unmount path.
 */
function mockNeverResolvingResponse(signal) {
  const reader = {
    read: () => new Promise((_resolve, reject) => {
      if (signal.aborted) {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
        return
      }
      signal.addEventListener('abort', () => {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
      })
    }),
    releaseLock: () => {}
  }
  return { body: { getReader: () => reader } }
}

/**
 * Drive the wizard from step 1 to step 6. Every Next press is an act();
 * step 4 needs a non-empty userName before its Next button activates.
 */
async function navigateToStep6() {
  // Step 1 → Step 2
  await act(async () => fireEvent.click(screen.getByRole('button', { name: /^Next$/ })))
  // Step 2 → Step 3
  await act(async () => fireEvent.click(screen.getByRole('button', { name: /^Next$/ })))
  // Step 3 → Step 4
  await act(async () => fireEvent.click(screen.getByRole('button', { name: /^Next$/ })))
  // Step 4: type a name so Next becomes enabled
  const nameInput = screen.getByPlaceholderText('Enter your name')
  await act(async () => fireEvent.change(nameInput, { target: { value: 'Tester' } }))
  // Step 4 → Step 5
  await act(async () => fireEvent.click(screen.getByRole('button', { name: /^Next$/ })))
  // Step 5 → Step 6
  await act(async () => fireEvent.click(screen.getByRole('button', { name: /^Next$/ })))
}

/**
 * Build a URL-dispatched fetch mock. Step 2 of the wizard fetches
 * `/api/templates` and `/api/extensions/catalog` BEFORE the diagnostic
 * stream POST, so a one-shot `mockImplementationOnce` would be consumed
 * by the wrong call. Routing by URL is the only stable pattern here.
 *
 * `setupHandler(url, opts)` is the per-test override that handles
 * `POST /api/setup/test`. Everything else falls through to the default
 * fail-soft handlers for templates/extensions and an empty-OK default.
 */
function makeFetchMock(setupHandler) {
  return vi.fn((url, opts) => {
    if (typeof url === 'string' && url.startsWith('/api/setup/test')) {
      return setupHandler(url, opts)
    }
    if (typeof url === 'string' && url.startsWith('/api/templates')) {
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) })
    }
    if (typeof url === 'string' && url.startsWith('/api/extensions')) {
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function stubFetchWithSetupStream(chunks) {
  vi.stubGlobal('fetch', makeFetchMock(() =>
    Promise.resolve(mockStreamResponse(chunks))
  ))
}

describe('SetupWizard diagnostics sentinel parser', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  test('PASS sentinel marks the run successful', async () => {
    stubFetchWithSetupStream([
      'foo\n',
      '__ODS_RESULT__:PASS:0\n'
    ])

    render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    await waitFor(() => {
      expect(screen.getByText(/All systems operational/i)).toBeInTheDocument()
    })
    // Sentinel itself must NOT be displayed to the user.
    expect(screen.queryByText(/__ODS_RESULT__/)).not.toBeInTheDocument()
    expect(screen.getByText('foo')).toBeInTheDocument()
  })

  test('FAIL sentinel marks the run as failed and surfaces the failure UI', async () => {
    stubFetchWithSetupStream([
      'bar\n',
      '__ODS_RESULT__:FAIL:3\n'
    ])

    render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    await waitFor(() => {
      expect(screen.getByText(/Some tests failed/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/__ODS_RESULT__/)).not.toBeInTheDocument()
    // After failure the bottom button flips from "Complete Setup" to
    // "Continue Anyway" and is enabled — so a transient diagnostic failure
    // doesn't lock the user out of the wizard. A "Re-run Diagnostics" button
    // also appears next to the failure banner.
    expect(screen.queryByRole('button', { name: /^Complete Setup$/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue Anyway/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Re-run Diagnostics/i })).toBeEnabled()
  })

  test('clicking Continue Anyway after a failed diagnostic completes the wizard', async () => {
    stubFetchWithSetupStream([
      'bar\n',
      '__ODS_RESULT__:FAIL:3\n'
    ])

    const onComplete = vi.fn()
    render(<SetupWizard onComplete={onComplete} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))
    await waitFor(() => expect(screen.getByText(/Some tests failed/i)).toBeInTheDocument())

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Continue Anyway/i })))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  test('falls back to "All tests passed!" trailer when sentinel missing (older backend)', async () => {
    stubFetchWithSetupStream([
      'starting...\n',
      'All tests passed!\n'
    ])

    render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    await waitFor(() => {
      expect(screen.getByText(/All systems operational/i)).toBeInTheDocument()
    })
  })

  test('defaults to failure when neither sentinel nor "All tests passed" appears', async () => {
    stubFetchWithSetupStream([
      'doing something\n',
      'partial output\n'
    ])

    render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    await waitFor(() => {
      expect(screen.getByText(/Some tests failed/i)).toBeInTheDocument()
    })
  })

  test('sentinel split across two chunks still parses', async () => {
    stubFetchWithSetupStream([
      'output line\n__ODS_RESULT__:PA',
      'SS:0\n'
    ])

    render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    await waitFor(() => {
      expect(screen.getByText(/All systems operational/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/__ODS_RESULT__/)).not.toBeInTheDocument()
  })

  test('unmount aborts the in-flight diagnostic fetch', async () => {
    let capturedSignal = null
    vi.stubGlobal('fetch', makeFetchMock((_url, opts) => {
      capturedSignal = opts?.signal ?? null
      return Promise.resolve(mockNeverResolvingResponse(opts.signal))
    }))

    const { unmount } = render(<SetupWizard onComplete={() => {}} />)
    await navigateToStep6()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Start Diagnostics/i })))

    expect(capturedSignal).not.toBeNull()
    expect(capturedSignal.aborted).toBe(false)

    // Unmount should fire the cleanup which aborts the controller.
    await act(async () => { unmount() })

    expect(capturedSignal.aborted).toBe(true)
  })
})
