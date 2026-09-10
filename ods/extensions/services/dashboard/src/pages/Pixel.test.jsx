import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import { act } from '@testing-library/react'
import { StrictMode } from 'react'

// The repository's base ESLint profile does not mark JSX identifiers as uses.
// eslint-disable-next-line no-unused-vars
import Pixel, {
  OperationsApprovalCard,
  formatElapsed,
  isCleanContextRecoveryFrame,
  parseApprovalReceipt,
  parseVerifiedPreviewFrame,
  resolvePreviewAccess,
} from './Pixel'

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
})

// Build a fake fetch Response with streaming SSE body
const sseResponse = (frames, { status = 200, chunks } = {}) => {
  const encoder = new TextEncoder()
  const frameBytes = frames.map(f => encoder.encode(`data: ${f}\n\n`))
  const concatFrames = (group) => {
    const totalLen = group.reduce((acc, i) => acc + frameBytes[i].byteLength, 0)
    const out = new Uint8Array(totalLen)
    let offset = 0
    for (const i of group) {
      out.set(frameBytes[i], offset)
      offset += frameBytes[i].byteLength
    }
    return out
  }
  const chunkGroups = chunks
    ? chunks.map(group => concatFrames(group))
    : frameBytes
  let idx = 0
  const reader = {
    read: async () => {
      if (idx >= chunkGroups.length) return { done: true, value: undefined }
      return { done: false, value: chunkGroups[idx++] }
    },
    releaseLock: () => {},
  }
  return {
    ok: status >= 200 && status < 300,
    status,
    body: { getReader: () => reader },
    headers: new Map([['content-type', 'text/event-stream']]),
  }
}

describe('Pixel', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
    globalThis.localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('formats short and long owner-agent turn durations', () => {
    expect(formatElapsed(0)).toBe('0:00')
    expect(formatElapsed(71)).toBe('1:11')
    expect(formatElapsed(3671)).toBe('1:01:11')
  })

  it('accepts only the fixed approval receipt grammar', () => {
    const jobId = 'ops-1788127319657-f3262c99a419'
    const planHash = 'e'.repeat(64)
    const content = `Pixel prepared the exact ods.extensions.install plan for extension crewai, but external approval is required. No lifecycle change was executed. Job: ${jobId}. Plan SHA-256: ${planHash}.`
    expect(parseApprovalReceipt(content)).toEqual({
      action: 'ods.extensions.install',
      extensionId: 'crewai',
      jobId,
      planHash,
    })
    expect(parseApprovalReceipt(`${content} Approve it now.`)).toBeNull()
    expect(parseApprovalReceipt(content.replace('crewai', '../../shadow'))).toBeNull()
    const hostCommand = `Pixel prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: ${jobId}. Plan SHA-256: ${planHash}.`
    expect(parseApprovalReceipt(hostCommand)).toEqual({
      action: 'raw-shell',
      extensionId: 'ods-host',
      jobId,
      planHash,
    })
    expect(parseApprovalReceipt(hostCommand.replace('No command was executed.', 'Command completed.'))).toBeNull()
  })

  it('accepts only the exact host-authored clean-context terminal marker', () => {
    const frame = {
      choices: [{ delta: {}, finish_reason: 'stop' }],
      pixel: {
        schemaVersion: 1,
        recovery: 'clean-context',
        reason: 'operations-unavailable-zero-submissions',
      },
    }
    expect(isCleanContextRecoveryFrame(frame)).toBe(true)
    expect(isCleanContextRecoveryFrame({
      ...frame,
      pixel: { ...frame.pixel, extra: true },
    })).toBe(false)
    expect(isCleanContextRecoveryFrame({
      ...frame,
      choices: [{ delta: {}, finish_reason: null }],
    })).toBe(false)
    expect(isCleanContextRecoveryFrame({
      ...frame,
      pixel: { ...frame.pixel, reason: 'model-prose-matched' },
    })).toBe(false)
  })

  it('accepts only an exact host-authored workspace preview terminal marker', () => {
    const sha256 = 'a'.repeat(64)
    const siteId = `site-${sha256.slice(0, 24)}`
    const preview = {
      schemaVersion: 1,
      kind: 'ods-pixel-workspace-preview',
      relativeDirectory: 'demo-website',
      siteId,
      port: 9437,
      url: `http://${siteId}.localhost:9437/${siteId}/`,
      files: 3,
      bytes: 4096,
      sha256,
      entrySha256: 'b'.repeat(64),
    }
    const frame = {
      choices: [{ delta: {}, finish_reason: 'stop' }],
      pixel: { schemaVersion: 1, preview },
    }
    expect(parseVerifiedPreviewFrame(frame)).toEqual(preview)
    expect(parseVerifiedPreviewFrame({
      ...frame,
      pixel: { schemaVersion: 1, preview: { ...preview, url: 'https://attacker.example/' } },
    })).toBeNull()
    const mismatchedSiteId = 'site-0123456789abcdef01234567'
    expect(parseVerifiedPreviewFrame({
      ...frame,
      pixel: {
        schemaVersion: 1,
        preview: {
          ...preview,
          siteId: mismatchedSiteId,
          url: `http://${mismatchedSiteId}.localhost:9437/${mismatchedSiteId}/`,
        },
      },
    })).toBeNull()
    expect(parseVerifiedPreviewFrame({
      ...frame,
      pixel: { schemaVersion: 1, preview, extra: true },
    })).toBeNull()
  })

  it('restores the verified preview after reload and preserves an explicit close', async () => {
    const sha256 = 'a'.repeat(64)
    const siteId = `site-${sha256.slice(0, 24)}`
    const preview = {
      schemaVersion: 1,
      kind: 'ods-pixel-workspace-preview',
      relativeDirectory: 'demo-website',
      siteId,
      port: 9437,
      url: `http://${siteId}.localhost:9437/${siteId}/`,
      files: 3,
      bytes: 4096,
      sha256,
      entrySha256: 'b'.repeat(64),
    }
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: `[Open the verified preview](${preview.url})\n\n[Documentation](https://example.com/docs)\n\n[Other host](http://${siteId}.localhost.example.com:9437/${siteId}/)` } }] }),
      JSON.stringify({
        choices: [{ delta: {}, finish_reason: 'stop' }],
        pixel: { schemaVersion: 1, preview },
      }),
      '[DONE]',
    ]))

    const original = render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Build and show me a demo website.' },
    })
    fireEvent.click(screen.getByTitle('Send'))

    const frame = await screen.findByTitle('Interactive Pixel preview')
    expect(frame).toHaveAttribute('src', `/pixel-preview/${siteId}/`)
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-forms allow-downloads')
    expect(screen.getByText('Host verified · 3 files')).toBeInTheDocument()
    expect(screen.getByTitle('Open preview in a new tab')).toHaveAttribute('href', `/pixel-preview/${siteId}/`)
    expect(screen.getByRole('link', { name: 'Open the verified preview' })).toHaveAttribute('href', `/pixel-preview/${siteId}/`)
    expect(screen.getByRole('link', { name: 'Documentation' })).toHaveAttribute('href', 'https://example.com/docs')
    expect(screen.getByRole('link', { name: 'Other host' })).toHaveAttribute('href', `http://${siteId}.localhost.example.com:9437/${siteId}/`)

    await waitFor(() => expect(
      JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1')).preview
    ).toEqual(preview))
    original.unmount()
    globalThis.fetch.mockResolvedValue(response({ available: true }))
    const restored = render(<Pixel />)
    expect(await screen.findByTitle('Interactive Pixel preview')).toHaveAttribute('src', `/pixel-preview/${siteId}/`)
    expect(globalThis.fetch.mock.calls.filter(([url]) => url === '/api/pixel/chat/stream')).toHaveLength(1)

    fireEvent.click(screen.getByTitle('Close preview'))
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
    await waitFor(() => expect(
      JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1')).preview
    ).toBeNull())
    restored.unmount()
    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the verified preview' })).toHaveAttribute('href', `/pixel-preview/${siteId}/`)
  })

  it('renders agent tables and task lists while keeping unsafe content inert', async () => {
    const content = [
      '**Files in log-lab:**',
      '| File | Size | Description |',
      '|------|-----:|-------------|',
      '| `log_analyzer.py` | 7263 B | Unicode 世界 |',
      '| [unsafe](javascript:alert%281%29) | 0 | <img src=x onerror="alert(1)"> |',
      '',
      '- [x] Export completed',
      '- [ ] Review output',
      '',
      '```python',
      'print("hello")',
      '```',
    ].join('\n')
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1,
      chatId: 'table-regression',
      messages: [{ role: 'assistant', content }],
    }))
    globalThis.fetch.mockResolvedValue(response({ available: true, model: 'pixel/default' }))
    const { container } = render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'File' })).toBeInTheDocument()
    expect(within(table).getByRole('cell', { name: '7263 B' })).toBeInTheDocument()
    expect(within(table).getByText('log_analyzer.py').tagName).toBe('CODE')
    expect(within(table).getByText('Unicode 世界')).toBeInTheDocument()
    expect(within(table).queryByRole('link', { name: 'unsafe' })).not.toBeInTheDocument()
    expect(container.querySelector('img[src="x"]')).toBeNull()
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes).toHaveLength(2)
    expect(checkboxes[0]).toBeChecked()
    expect(checkboxes[1]).not.toBeChecked()
    checkboxes.forEach(checkbox => expect(checkbox).toBeDisabled())
    expect(container.querySelector('pre code')).toHaveTextContent('print("hello")')
  })

  it.each([
    { url: 'https://attacker.example/' },
    { sha256: 42 },
    { relativeDirectory: '../outside' },
    { siteId: 'site-0123456789abcdef01234567' },
  ])('keeps the conversation but rejects damaged cached preview metadata: %j', async (changed) => {
    const sha256 = 'a'.repeat(64)
    const siteId = `site-${sha256.slice(0, 24)}`
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1,
      chatId: 'saved_preview',
      messages: [{ role: 'user', content: 'Keep my work visible' }],
      preview: {
        schemaVersion: 1, kind: 'ods-pixel-workspace-preview',
        relativeDirectory: 'demo', siteId, port: 9437,
        url: `http://${siteId}.localhost:9437/${siteId}/`,
        files: 1, bytes: 2048, sha256, entrySha256: 'b'.repeat(64),
        ...changed,
      },
    }))
    globalThis.fetch.mockResolvedValue(response({ available: true }))
    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('Keep my work visible')).toBeInTheDocument()
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
  })

  it('clears the stored preview when starting a new conversation', async () => {
    const sha256 = 'a'.repeat(64)
    const siteId = `site-${sha256.slice(0, 24)}`
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1, chatId: 'saved_preview', messages: [{ role: 'user', content: 'Old project' }],
      preview: {
        schemaVersion: 1, kind: 'ods-pixel-workspace-preview',
        relativeDirectory: 'demo', siteId, port: 9437,
        url: `http://${siteId}.localhost:9437/${siteId}/`,
        files: 1, bytes: 2048, sha256, entrySha256: 'b'.repeat(64),
      },
    }))
    globalThis.fetch.mockResolvedValue(response({ available: true }))
    const restored = render(<Pixel />)
    expect(await screen.findByTitle('Interactive Pixel preview')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Start a new chat'))
    await waitFor(() => {
      const stored = JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1'))
      expect(stored.preview).toBeNull()
      expect(stored.messages).toEqual([])
      expect(stored.chatId).not.toBe('saved_preview')
    })
    restored.unmount()
    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
  })

  it('uses the authenticated relay for LAN and forwarded loopback dashboards', () => {
    const preview = {
      siteId: 'site-0123456789abcdef01234567',
      url: 'http://site-0123456789abcdef01234567.localhost:9437/site-0123456789abcdef01234567/',
    }
    expect(resolvePreviewAccess(preview, {
      hostname: 'dashboard.ods.local',
      protocol: 'http:',
    })).toEqual({
      url: '/pixel-preview/site-0123456789abcdef01234567/',
      sandbox: 'allow-scripts allow-forms allow-downloads',
      route: 'private-dashboard',
    })
    for (const hostname of ['localhost', '127.0.0.1', '[::1]', '::1']) {
      expect(resolvePreviewAccess(preview, { hostname, protocol: 'http:' })).toEqual({
        url: `/pixel-preview/${preview.siteId}/`,
        sandbox: 'allow-scripts allow-forms allow-downloads',
        route: 'private-dashboard',
      })
    }
  })

  it('does not open a preview from model-authored localhost text', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Live at http://localhost:3000/demo/' } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Show my site.' },
    })
    fireEvent.click(screen.getByTitle('Send'))
    expect(await screen.findByText(/Live at/)).toBeInTheDocument()
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
  })

  it('renders a host-verified approval card without approving in the browser', async () => {
    const jobId = 'ops-1788127319657-f3262c99a419'
    const planHash = 'e'.repeat(64)
    const command = `/opt/ods/bin/ods-pixel-approve ${jobId} ${planHash} --confirm`
    const content = `Pixel prepared the exact ods.extensions.install plan for extension crewai, but external approval is required. No lifecycle change was executed. Job: ${jobId}. Plan SHA-256: ${planHash}.`
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) }
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      configurable: true,
      value: clipboard,
    })
    globalThis.fetch.mockImplementation(async (url) => {
      if (url === '/api/pixel/status') {
        return response({ available: true, model: 'pixel/default', detail: 'local' })
      }
      if (url === '/api/pixel/chat/stream') {
        return sseResponse([
          JSON.stringify({ choices: [{ delta: { content } }] }),
          '[DONE]',
        ])
      }
      if (url === `/api/pixel/ops/${jobId}?plan_hash=${planHash}`) {
        return response({
          schemaVersion: 1,
          kind: 'ods-pixel-operations-status',
          jobId,
          planHash,
          status: 'awaiting-approval',
          riskTier: 'managed',
          approvalRequired: true,
          updatedAt: '2026-08-30T22:01:59Z',
          approvalCommand: command,
        })
      }
      throw new Error(`unexpected fetch ${url}`)
    })

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Install the ODS extension crewai.' },
    })
    fireEvent.click(screen.getByTitle('Send'))

    expect(await screen.findByText('Owner approval required')).toBeInTheDocument()
    expect(screen.getByText('managed')).toBeInTheDocument()
    const copy = screen.getByRole('button', { name: 'Copy secure approval command' })
    fireEvent.click(copy)
    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalledWith(command))
    expect(globalThis.fetch.mock.calls.some(([url]) => url.includes('/api/pixel/ops/'))).toBe(true)
    expect(globalThis.fetch.mock.calls.some(([, options]) => options?.method === 'POST' && options?.body?.includes('approve'))).toBe(false)
  })

  it('renders the same independently verified approval UX for a protected host command', async () => {
    const jobId = 'ops-1788127319657-f3262c99a419'
    const planHash = 'f'.repeat(64)
    const command = `/opt/ods/bin/ods-pixel-approve ${jobId} ${planHash} --confirm`
    const content = `Pixel prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: ${jobId}. Plan SHA-256: ${planHash}.`
    globalThis.fetch.mockResolvedValue(response({
      schemaVersion: 1,
      kind: 'ods-pixel-operations-status',
      jobId,
      planHash,
      status: 'awaiting-approval',
      riskTier: 'break-glass',
      approvalRequired: true,
      updatedAt: '2026-09-03T02:00:00Z',
      approvalCommand: command,
    }))

    render(<OperationsApprovalCard content={content} />)

    expect(await screen.findByText('Owner approval required')).toBeInTheDocument()
    expect(screen.getByText('raw-shell · ods-host')).toBeInTheDocument()
    expect(screen.getByText('break-glass')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy secure approval command' })).toBeInTheDocument()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      `/api/pixel/ops/${jobId}?plan_hash=${planHash}`,
      expect.any(Object),
    )
  })

  it('recovers an approval card after a transient status projection failure', async () => {
    vi.useFakeTimers()
    const jobId = 'ops-1788127319657-f3262c99a419'
    const planHash = 'e'.repeat(64)
    const content = `Pixel prepared the exact ods.extensions.install plan for extension crewai, but external approval is required. No lifecycle change was executed. Job: ${jobId}. Plan SHA-256: ${planHash}.`
    globalThis.fetch
      .mockRejectedValueOnce(new Error('temporary restart'))
      .mockResolvedValueOnce(response({
        schemaVersion: 1,
        kind: 'ods-pixel-operations-status',
        jobId,
        planHash,
        status: 'succeeded',
        riskTier: 'managed',
        approvalRequired: true,
        updatedAt: '2026-08-30T22:01:59Z',
        approvalCommand: null,
      }))

    render(<OperationsApprovalCard content={content} />)
    await act(async () => {})
    expect(screen.getByRole('alert')).toHaveTextContent(
      'This approval receipt could not be independently verified. Do not approve it.'
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(screen.getByText('Protected operation completed')).toBeInTheDocument()
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('shows unavailable state when status fails', async () => {
    globalThis.fetch.mockResolvedValue(response({ available: false, detail: 'Edge unreachable' }))

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Degraded')).toBeInTheDocument()
    })
    expect(screen.getAllByText(/Edge unreachable/).length).toBeGreaterThan(0)
  })

  it('shows available state when status succeeds', async () => {
    globalThis.fetch.mockResolvedValue(response({ available: true, model: 'pixel/default', detail: 'local' }))

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })
  })

  it('shows a model-switching state and keeps the composer disabled', async () => {
    globalThis.fetch.mockResolvedValue(response({
      available: false,
      model: null,
      state: 'model_switching',
      detail: 'Model switch in progress; Pixel will be ready when activation completes',
    }))

    render(<Pixel />)

    await waitFor(() => expect(screen.getByText('Switching model...')).toBeInTheDocument())
    expect(screen.getByText('Pixel is switching models')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Waiting for model switch...')).toBeDisabled()
  })

  it('keeps an adaptive model available without presenting a warning gate', async () => {
    globalThis.fetch.mockResolvedValue(response({
      available: true,
      model: 'pixel/default',
      detail: 'Owner agent ready',
      modelSupport: {
        tier: 'adaptive',
        detail: 'Pixel is ready and adapts its tool flow for this model.',
      },
    }))

    render(<Pixel />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Available')).toHaveAttribute(
      'title',
      'Pixel is ready and adapts its tool flow for this model.'
    )
    expect(screen.getAllByRole('link', { name: 'Change model' })).toHaveLength(1)
    expect(screen.getAllByRole('link', { name: 'Change model' })[0]).toHaveAttribute('href', '/models')
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeEnabled()
  })

  it('preserves a draft when model viability changes before stream acceptance', async () => {
    globalThis.fetch
      .mockResolvedValueOnce(response({ available: true, model: 'pixel/default', detail: 'local' }))
      .mockResolvedValueOnce(response({
        detail: 'The active model is not qualified for Pixel tool use.',
      }, 412))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    const composer = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(composer, { target: { value: 'keep this owner request' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeEnabled()
    expect(screen.getByPlaceholderText('Message Pixel...')).toHaveValue(
      'keep this owner request'
    )
  })

  it('maps the legacy incompatible status to a usable adaptive status', async () => {
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1,
      chatId: 'stored_chat',
      messages: [
        { role: 'user', content: 'old request' },
        { role: 'assistant', content: 'old response' },
      ],
    }))
    globalThis.fetch.mockResolvedValue(response({
      available: false,
      model: null,
      state: 'model_incompatible',
      detail: 'This model failed Pixel tool qualification.',
    }))

    render(<Pixel />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Available')).toHaveAttribute(
      'title',
      'This model failed Pixel tool qualification.'
    )
    expect(screen.getAllByRole('link', { name: 'Change model' })).toHaveLength(1)
    expect(screen.getAllByRole('link', { name: 'Change model' })[0]).toHaveAttribute('href', '/models')
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeEnabled()
  })

  it('restores an unsent draft when model activation wins the chat race', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(response({
      detail: 'Model switch in progress; Pixel will be ready when activation completes',
    }, 409))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'keep this exact draft' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => expect(screen.getByText('Switching model...')).toBeInTheDocument())
    expect(textarea).toHaveValue('keep this exact draft')
    expect(screen.queryByText('Request failed')).not.toBeInTheDocument()
    expect(JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1')).messages).toEqual([])
  })

  it('keeps send disabled until the draft has non-whitespace content', async () => {
    globalThis.fetch.mockResolvedValue(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    const send = screen.getByTitle('Send')
    expect(send).toBeDisabled()
    fireEvent.change(textarea, { target: { value: '   ' } })
    expect(send).toBeDisabled()
    fireEvent.change(textarea, { target: { value: 'ready' } })
    expect(send).toBeEnabled()
  })

  it('restores the bounded local chat and reuses its opaque session after reload', async () => {
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1,
      chatId: 'persisted-chat-42',
      messages: [
        { role: 'user', content: 'remember this' },
        { role: 'assistant', content: 'remembered' },
      ],
    }))
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'still remembered' } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('remembered')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'what did I say?' },
    })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('still remembered')).toBeInTheDocument())

    const chatCall = globalThis.fetch.mock.calls.find(call => call[0] === '/api/pixel/chat/stream')
    const body = JSON.parse(chatCall[1].body)
    expect(body.chat_id).toBe('persisted-chat-42')
    expect(body.messages).toEqual([
      { role: 'user', content: 'remember this' },
      { role: 'assistant', content: 'remembered' },
      { role: 'user', content: 'what did I say?' },
    ])
  })

  it('orients the owner with live runtime identity and editable starter tasks', async () => {
    globalThis.fetch.mockResolvedValue(
      response({ available: true, model: 'pixel/default', detail: 'Owner agent ready' })
    )

    render(<Pixel systemStatus={{
      inference: {
        loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
        contextSize: 32768,
      },
    }} />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('What should we accomplish?')).toBeInTheDocument()
    expect(screen.getByText('Qwen3.5-9B-Q4_K_M.gguf')).toBeInTheDocument()
    expect(screen.getByText('32K context')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Check ODS health/ }))
    expect(screen.getByPlaceholderText('Message Pixel...')).toHaveValue(
      'Check the current ODS status. Summarize what is healthy, identify anything degraded or stopped, and suggest the safest next action.'
    )
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('shows the active remote Pixel runtime instead of the local rollback model', async () => {
    globalThis.fetch.mockResolvedValue(
      response({
        available: true,
        model: 'pixel/default',
        detail: 'Owner agent ready',
        runtime: {
          source: 'remote-provider',
          model: 'remote-owner-model',
          contextLength: 131072,
          maxTokens: 16384,
          reasoning: false,
        },
      })
    )

    render(<Pixel systemStatus={{
      inference: {
        loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
        contextSize: 32768,
      },
    }} />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('remote-owner-model')).toBeInTheDocument()
    expect(screen.getByText('128K context')).toBeInTheDocument()
    expect(screen.queryByText('Qwen3.5-9B-Q4_K_M.gguf')).not.toBeInTheDocument()
  })

  it('shows a callable 8K remote model without imposing a larger context floor', async () => {
    globalThis.fetch.mockResolvedValue(
      response({
        available: true,
        model: 'pixel/default',
        detail: 'Owner agent ready',
        runtime: {
          source: 'remote-provider',
          model: 'small-owner-model',
          contextLength: 8192,
          maxTokens: 1024,
          reasoning: false,
        },
        modelSupport: {
          tier: 'adaptive',
          detail: 'Pixel is ready and adapts its tool flow for this model.',
        },
      })
    )

    render(<Pixel systemStatus={{
      inference: {
        loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
        contextSize: 32768,
      },
    }} />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('small-owner-model')).toBeInTheDocument()
    expect(screen.getByText('8K context')).toBeInTheDocument()
    expect(screen.queryByText('Qwen3.5-9B-Q4_K_M.gguf')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeEnabled()
  })

  it('shows the verified local runtime instead of stale installer model metadata', async () => {
    globalThis.fetch.mockResolvedValue(response({
      available: true,
      runtime: { source: 'local-switchboard', model: 'Qwen3.6-35B-A3B-GGUF', contextLength: 65536 },
      modelSupport: { tier: 'adaptive', detail: 'Pixel adapts its tools to this model.' },
    }))
    render(<Pixel systemStatus={{ inference: { loadedModel: 'qwen3.5-9b', contextSize: 32768 } }} />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('Qwen3.6-35B-A3B-GGUF')).toBeInTheDocument()
    expect(screen.getByText('64K context')).toBeInTheDocument()
    expect(screen.queryByText('qwen3.5-9b')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeEnabled()
  })

  it('ignores an unknown runtime source and keeps the fallback local identity', async () => {
    globalThis.fetch.mockResolvedValue(
      response({
        available: true,
        model: 'pixel/default',
        detail: 'Owner agent ready',
        runtime: {
          source: 'local',
          model: 'forged-runtime',
          contextLength: 131072,
          maxTokens: 16384,
          reasoning: false,
        },
      })
    )

    render(<Pixel systemStatus={{
      inference: {
        loadedModel: 'Qwen3.5-9B-Q4_K_M.gguf',
        contextSize: 32768,
      },
    }} />)

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('Qwen3.5-9B-Q4_K_M.gguf')).toBeInTheDocument()
    expect(screen.getByText('32K context')).toBeInTheDocument()
    expect(screen.queryByText('forged-runtime')).not.toBeInTheDocument()
  })

  it('sends exact body to stream endpoint', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    // Prepare stream response
    const streamFrames = [
      JSON.stringify({ choices: [{ delta: { content: 'Hello' } }] }),
      '[DONE]',
    ]
    globalThis.fetch.mockResolvedValueOnce(sseResponse(streamFrames))

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'hi there' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/pixel/chat/stream',
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        })
      )
    })

    const call = globalThis.fetch.mock.calls.find(
      c => c[0] === '/api/pixel/chat/stream'
    )
    const body = JSON.parse(call[1].body)
    expect(body.messages).toHaveLength(1)
    expect(body.messages[0].role).toBe('user')
    expect(body.messages[0].content).toBe('hi there')
    expect(body.chat_id).toBeDefined()
    expect(body.request_id).toEqual(expect.any(String))
    expect(call[1].headers).toEqual({ 'Content-Type': 'application/json' })
    expect(call[1].headers.Authorization).toBeUndefined()
  })

  it('sends only role and content on later turns', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'First answer' } }] }),
      '[DONE]',
    ]))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Second answer' } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'first turn' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('First answer')).toBeInTheDocument())

    fireEvent.change(textarea, { target: { value: 'second turn' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('Second answer')).toBeInTheDocument())

    const chatCalls = globalThis.fetch.mock.calls.filter(
      call => call[0] === '/api/pixel/chat/stream'
    )
    expect(chatCalls).toHaveLength(2)
    const body = JSON.parse(chatCalls[1][1].body)
    expect(body.messages).toEqual([
      { role: 'user', content: 'first turn' },
      { role: 'assistant', content: 'First answer' },
      { role: 'user', content: 'second turn' },
    ])
    expect(body.messages.every(message => Object.keys(message).sort().join(',') === 'content,role')).toBe(true)
  })

  it('recovers once from a host-authoritative zero-submission marker with clean future context', async () => {
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1,
      chatId: 'long-running-chat',
      messages: [
        { role: 'user', content: 'old context' },
        { role: 'assistant', content: 'old answer' },
      ],
    }))
    const marker = JSON.stringify({
      choices: [{ delta: {}, finish_reason: 'stop' }],
      pixel: {
        schemaVersion: 1,
        recovery: 'clean-context',
        reason: 'operations-unavailable-zero-submissions',
      },
    })
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'unverified model prose' } }] }),
      marker,
      '[DONE]',
    ]))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Verified recovery result' } }] }),
      '[DONE]',
    ]))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Follow-up result' } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'Inspect the installed extension.' } })
    fireEvent.click(screen.getByTitle('Send'))

    expect(await screen.findByText('Verified recovery result')).toBeInTheDocument()
    expect(screen.getByText('Recovered with a clean context')).toBeInTheDocument()
    expect(screen.queryByText('unverified model prose')).not.toBeInTheDocument()
    const firstTwo = globalThis.fetch.mock.calls.filter(call => call[0] === '/api/pixel/chat/stream')
    expect(firstTwo).toHaveLength(2)
    const firstBody = JSON.parse(firstTwo[0][1].body)
    const retryBody = JSON.parse(firstTwo[1][1].body)
    expect(retryBody.chat_id).not.toBe(firstBody.chat_id)
    expect(retryBody.messages).toEqual([
      { role: 'user', content: 'Inspect the installed extension.' },
    ])

    fireEvent.change(textarea, { target: { value: 'Continue from that verified result.' } })
    fireEvent.click(screen.getByTitle('Send'))
    expect(await screen.findByText('Follow-up result')).toBeInTheDocument()
    const chatCalls = globalThis.fetch.mock.calls.filter(call => call[0] === '/api/pixel/chat/stream')
    expect(chatCalls).toHaveLength(3)
    expect(JSON.parse(chatCalls[2][1].body)).toEqual({
      chat_id: retryBody.chat_id,
      request_id: expect.any(String),
      messages: [
        { role: 'user', content: 'Inspect the installed extension.' },
        { role: 'assistant', content: 'Verified recovery result' },
        { role: 'user', content: 'Continue from that verified result.' },
      ],
    })
  })

  it('stops honestly after a second host-authoritative zero-submission marker', async () => {
    const marker = JSON.stringify({
      choices: [{ delta: {}, finish_reason: 'stop' }],
      pixel: {
        schemaVersion: 1,
        recovery: 'clean-context',
        reason: 'operations-unavailable-zero-submissions',
      },
    })
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([marker, '[DONE]']))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([marker, '[DONE]']))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Inspect ODS through Operations.' },
    })
    fireEvent.click(screen.getByTitle('Send'))

    expect(await screen.findByText(/Automatic recovery was attempted once/)).toBeInTheDocument()
    expect(globalThis.fetch.mock.calls.filter(call => call[0] === '/api/pixel/chat/stream')).toHaveLength(2)
    expect(screen.queryByText('Recovered with a clean context')).not.toBeInTheDocument()
  })

  it('never retries from matching model prose without the structured host marker', async () => {
    const prose = 'Pixel did not submit the requested host or Operations work through the isolated Operations Broker.'
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: prose } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Discuss the fallback wording.' },
    })
    fireEvent.click(screen.getByTitle('Send'))

    expect(await screen.findByText(prose)).toBeInTheDocument()
    expect(globalThis.fetch.mock.calls.filter(call => call[0] === '/api/pixel/chat/stream')).toHaveLength(1)
  })

  it('starts a clean conversation with a new opaque chat id', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'First answer' } }] }),
      '[DONE]',
    ]))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Second answer' } }] }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'first turn' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('First answer')).toBeInTheDocument())

    const firstCall = globalThis.fetch.mock.calls.find(call => call[0] === '/api/pixel/chat/stream')
    const firstChatId = JSON.parse(firstCall[1].body).chat_id
    fireEvent.click(screen.getByTitle('Start a new chat'))
    expect(screen.queryByText('First answer')).not.toBeInTheDocument()
    expect(screen.getByText('What should we accomplish?')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'second turn' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('Second answer')).toBeInTheDocument())

    const chatCalls = globalThis.fetch.mock.calls.filter(call => call[0] === '/api/pixel/chat/stream')
    const secondChatId = JSON.parse(chatCalls[1][1].body).chat_id
    expect(secondChatId).not.toBe(firstChatId)
  })

  it('parses SSE chunks and displays content', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    const frames = [
      JSON.stringify({ choices: [{ delta: { content: 'Hello' } }] }),
      JSON.stringify({ choices: [{ delta: { content: ' world' } }] }),
      '[DONE]',
    ]
    globalThis.fetch.mockResolvedValueOnce(sseResponse(frames))

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'hello' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      expect(screen.getByText('Hello world')).toBeInTheDocument()
    })
  })

  it('parses chunks across arbitrary boundaries', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    // Split the JSON across two reader.read() calls
    const frames = [
      JSON.stringify({ choices: [{ delta: { content: 'Split' } }] }),
      JSON.stringify({ choices: [{ delta: { content: ' test' } }] }),
      '[DONE]',
    ]
    // First chunk has only partial first frame, second has rest
    const encoder = new TextEncoder()
    const full = frames.map(f => encoder.encode(`data: ${f}\n\n`))
    const firstFrame = full[0]
    const mid = Math.floor(firstFrame.length / 2)

    let idx = 0
    const chunks = [firstFrame.slice(0, mid), firstFrame.slice(mid), full[1], full[2]]

    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            if (idx >= chunks.length) return { done: true, value: undefined }
            return { done: false, value: chunks[idx++] }
          },
          releaseLock: () => {},
        }),
      },
      headers: new Map([['content-type', 'text/event-stream']]),
    })

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'boundaries' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      expect(screen.getByText('Split test')).toBeInTheDocument()
    })
  })

  it('preserves an in-flight request and partial answer across repeated reloads without replay', async () => {
    let releasePendingRead
    let reads = 0
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({ available: true })
      if (url === '/api/pixel/chat/activity') return response({ state: 'unknown' })
      if (url !== '/api/pixel/chat/stream') throw new Error(`unexpected request: ${url}`)
      const pendingRead = new Promise(resolve => { releasePendingRead = resolve })
      options.signal.addEventListener('abort', () => releasePendingRead({ done: true }), { once: true })
      return {
        ok: true,
        headers: new Map([['content-type', 'text/event-stream']]),
        body: { getReader: () => ({
          read: async () => reads++ === 0
            ? { done: false, value: new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Created report.md; checking its contents."}}]}\n\n') }
            : pendingRead,
          releaseLock: () => {},
        }) },
      }
    })
    const first = render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'Create a report, then verify it.' } })
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('Created report.md; checking its contents.')
    const request = JSON.parse(globalThis.fetch.mock.calls.find(([url]) => url === '/api/pixel/chat/stream')[1].body)
    await waitFor(() => {
      const saved = JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1'))
      expect(saved.chatId).toBe(request.chat_id)
      expect(saved.inFlight).toBe(true)
      expect(saved.messages).toEqual([
        { role: 'user', content: 'Create a report, then verify it.' },
        { role: 'assistant', content: 'Created report.md; checking its contents.' },
      ])
    })
    first.unmount()
    await act(async () => {})
    for (let reload = 0; reload < 2; reload += 1) {
      const restored = render(<Pixel />)
      await screen.findByText('Activity unknown')
      expect(screen.getByText('Create a report, then verify it.')).toBeInTheDocument()
      expect(screen.getByText('Created report.md; checking its contents.')).toBeInTheDocument()
      expect(screen.getByText(/Completion was not confirmed/)).toBeInTheDocument()
      expect(screen.queryByText(/Stopped by you/)).not.toBeInTheDocument()
      expect(screen.queryByTitle('Stop')).not.toBeInTheDocument()
      expect(globalThis.fetch.mock.calls.filter(([url]) => url === '/api/pixel/chat/stream')).toHaveLength(1)
      restored.unmount()
    }
  })

  it('recovers a completed answer after closing the original stream without resubmitting in StrictMode', async () => {
    const siteId = 'site-' + 'a'.repeat(24)
    const preview = {schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'recovery-demo',siteId,port:9437,
      url:`http://${siteId}.localhost:9437/${siteId}/`,files:2,bytes:4096,
      sha256:'a'.repeat(64),entrySha256:'b'.repeat(64)}
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema:1, chatId:'durable-chat', requestId:'durable-attempt', inFlight:true,
      messages:[{role:'user',content:'Make my preview'},{role:'assistant',content:'Partial answer'}],
    }))
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({available:true})
      if (url === '/api/pixel/chat/result') {
        expect(JSON.parse(options.body)).toEqual({chat_id:'durable-chat',request_id:'durable-attempt'})
        return response({state:'complete',events:[
          'data: '+JSON.stringify({choices:[{delta:{content:'Recovered final answer'}}]}),
          'data: '+JSON.stringify({choices:[{finish_reason:'stop'}],pixel:{schemaVersion:1,preview}}),
          'data: [DONE]', '',
        ].join('\n')})
      }
      throw new Error(`Unexpected request ${url}`)
    })
    const first = render(<StrictMode><Pixel /></StrictMode>)
    expect(await screen.findByText('Recovered final answer')).toBeVisible()
    expect(await screen.findByTitle('Interactive Pixel preview')).toHaveAttribute('src',`/pixel-preview/${siteId}/`)
    expect(screen.queryByText('Partial answer')).toBeNull()
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
    await waitFor(() => expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).inFlight).toBe(false))
    first.unmount()
    render(<Pixel />)
    expect(await screen.findByText('Recovered final answer')).toBeVisible()
    expect(screen.getAllByText('Recovered final answer')).toHaveLength(1)
  })

  it('does not call a retained zero-submission receipt completed or resubmit it on reload', async () => {
    localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema:1,chatId:'not-started-chat',requestId:'not-started-attempt',inFlight:true,
      messages:[{role:'user',content:'Do my task'},{role:'assistant',content:''}]}))
    const frame = {choices:[{delta:{},finish_reason:'stop'}],pixel:{schemaVersion:1,recovery:'clean-context',reason:'operations-unavailable-zero-submissions'}}
    globalThis.fetch.mockImplementation(async url => {
      if (url === '/api/pixel/status') return response({available:true})
      if (url === '/api/pixel/chat/result') return response({state:'complete',events:'data: '+JSON.stringify(frame)+'\n\ndata: [DONE]\n\n'})
      throw new Error(`Unexpected request ${url}`)
    })
    render(<Pixel />)
    expect(await screen.findByText('Pixel did not start this attempt. Send your message again to continue.')).toBeVisible()
    expect(screen.queryByText('Completed without a text response.')).toBeNull()
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  })

  it('commits its recovery identity before starting a request', async () => {
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({available:true})
      if (url === '/api/pixel/chat/stream') {
        const sent = JSON.parse(options.body)
        const saved = JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
        expect(saved.requestId).toBe(sent.request_id)
        expect(saved.chatId).toBe(sent.chat_id)
        expect(saved.inFlight).toBe(true)
        expect(saved.messages).toEqual([{role:'user',content:'Retain this task'},{role:'assistant',content:''}])
        return sseResponse([JSON.stringify({choices:[{delta:{content:'Stored'}}]}),'[DONE]'])
      }
      throw new Error(`Unexpected request ${url}`)
    })
    render(<Pixel />); await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Retain this task'}})
    fireEvent.click(screen.getByTitle('Send'))
    expect(await screen.findByText('Stored')).toBeVisible()
  })

  it('does not start orphaned work when the attempt identity cannot be saved', async () => {
    globalThis.fetch.mockResolvedValue(response({available:true}))
    render(<Pixel />); await screen.findByText('Available')
    vi.spyOn(window.Storage.prototype,'setItem').mockImplementation(() => { throw new Error('quota') })
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Keep my draft'}})
    fireEvent.click(screen.getByTitle('Send'))
    expect(await screen.findByText(/No task was started/)).toBeVisible()
    expect(screen.getByPlaceholderText('Message Pixel...')).toHaveValue('Keep my draft')
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  })

  function saveInterruptedChat() {
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({
      schema: 1, chatId: 'restored_opaque_chat', inFlight: true,
      messages: [{ role: 'user', content: 'Inspect my project' }, { role: 'assistant', content: 'Saved partial result' }],
    }))
  }

  it('restored activity tracks this chat from active to terminal without replay or stopped claims', async () => {
    saveInterruptedChat()
    let state = 'active'
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({ available: true })
      expect(url).toBe('/api/pixel/chat/activity')
      expect(JSON.parse(options.body)).toEqual({ chat_id: 'restored_opaque_chat' })
      return response({ state })
    })
    render(<Pixel />)
    await screen.findByText('Working in this chat')
    expect(screen.getByTitle('Stop')).toBeEnabled()
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeDisabled()
    expect(screen.getByTitle('Start a new chat')).toBeDisabled()
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    state = 'terminal'
    await screen.findByText(/previous request is no longer active/, {}, { timeout: 3000 })
    expect(screen.queryByTitle('Stop')).not.toBeInTheDocument()
    expect(screen.queryByText('Response stopped')).not.toBeInTheDocument()
    expect(screen.getByText(/final response was not recovered/)).toBeInTheDocument()
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  })

  it('restored activity recovers from unknown and only exact cancellation marks this chat stopped', async () => {
    saveInterruptedChat()
    let state = 'unknown'
    let aborted = false
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({ available: false, switching: true })
      expect(JSON.parse(options.body)).toEqual({ chat_id: 'restored_opaque_chat' })
      if (url === '/api/pixel/chat/activity') return response({ state })
      expect(url).toBe('/api/pixel/chat/cancel')
      return response({ aborted })
    })
    render(<Pixel />)
    await screen.findByText('Activity unknown')
    expect(screen.queryByTitle('Stop')).not.toBeInTheDocument()
    state = 'active'
    fireEvent.click(screen.getByText('Check activity again'))
    await screen.findByText('Working in this chat')
    expect(screen.getByRole('textbox')).toBeDisabled()
    fireEvent.click(screen.getByTitle('Stop'))
    await screen.findByRole('alert')
    expect(screen.queryByText('Response stopped')).not.toBeInTheDocument()
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    aborted = true
    fireEvent.click(screen.getByTitle('Stop'))
    await screen.findByText('Response stopped')
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  })

  it('restored activity terminal observation wins a pending cancellation race', async () => {
    saveInterruptedChat()
    let state = 'active'
    let finishCancel
    globalThis.fetch.mockImplementation(async (url) => {
      if (url === '/api/pixel/status') return response({ available: true })
      if (url === '/api/pixel/chat/activity') return response({ state })
      if (url === '/api/pixel/chat/cancel') return new Promise(resolve => { finishCancel = resolve })
      throw new Error('unexpected replay')
    })
    render(<Pixel />)
    await screen.findByTitle('Stop')
    fireEvent.click(screen.getByTitle('Stop'))
    state = 'terminal'
    await screen.findByText(/previous request is no longer active/, {}, { timeout: 3000 })
    await act(async () => { finishCancel(response({ aborted: true })) })
    expect(screen.queryByText('Response stopped')).not.toBeInTheDocument()
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    expect(screen.queryByTitle('Stop')).not.toBeInTheDocument()
  })

  it('restored activity rejects unrelated global activity and allows a failed lookup retry', async () => {
    saveInterruptedChat()
    let calls = 0
    globalThis.fetch.mockImplementation(async (url) => {
      if (url === '/api/pixel/status') return response({ available: true })
      expect(url).toBe('/api/pixel/chat/activity')
      calls += 1
      if (calls === 1) throw new Error('offline')
      if (calls === 2) return response({ active: true, streams: 1 })
      return response({ state: 'active' })
    })
    render(<Pixel />)
    await screen.findByText('Activity unknown')
    fireEvent.click(screen.getByText('Check activity again'))
    await screen.findByText('Activity unknown')
    expect(screen.queryByTitle('Stop')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Check activity again'))
    await screen.findByText('Working in this chat')
  })

  it('restored unknown activity permits an explicit exact-chat stop attempt without replay or false stopped claims', async () => {
    saveInterruptedChat()
    let aborted = false
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({ available: true })
      expect(JSON.parse(options.body)).toEqual({ chat_id: 'restored_opaque_chat' })
      if (url === '/api/pixel/chat/activity') return response({ state: 'unknown' })
      expect(url).toBe('/api/pixel/chat/cancel')
      return response({ aborted })
    })
    render(<Pixel />)
    await screen.findByText('Activity unknown')
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/cancel')).toBe(false)
    fireEvent.click(screen.getByText('Try Stop previous work'))
    await screen.findByRole('alert')
    expect(screen.queryByText('Response stopped')).not.toBeInTheDocument()
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    aborted = true
    fireEvent.click(screen.getByText('Try Stop previous work'))
    await screen.findByText('Response stopped')
    expect(screen.getByText('Saved partial result')).toBeInTheDocument()
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
  })

  it('disables send while streaming', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    // Stream that holds open
    const holdReader = {
      read: async () => new Promise(() => {}),
      releaseLock: () => {},
    }
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: { getReader: () => holdReader },
      headers: new Map([['content-type', 'text/event-stream']]),
    })

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'test' } })
    fireEvent.click(screen.getByTitle('Send'))

    // During streaming the Send button is replaced by a Stop button;
    // the textarea itself is disabled.
    await waitFor(() => {
      expect(screen.queryByTitle('Send')).not.toBeInTheDocument()
      const ta = screen.getByPlaceholderText('Message Pixel...')
      expect(ta).toBeDisabled()
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getAllByText(/0:00 elapsed/).length).toBeGreaterThan(0)
      expect(screen.getByText('Starting the owner-agent turn')).toBeInTheDocument()
      expect(screen.queryByText('Available')).not.toBeInTheDocument()
    })
  })

  it('renders an owner-stopped response as a neutral terminal state', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => new Promise(() => {}),
          releaseLock: () => {},
        }),
      },
      headers: new Map([['content-type', 'text/event-stream']]),
    })
    globalThis.fetch.mockResolvedValueOnce(response({ aborted: true }))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'long task' },
    })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByTitle('Stop')).toBeInTheDocument())
    fireEvent.click(screen.getByTitle('Stop'))

    const stopped = await screen.findByText('Response stopped')
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/pixel/chat/cancel', expect.objectContaining({
      method: 'POST',
    }))
    expect(stopped.parentElement).toHaveClass('bg-amber-500/10')
    expect(stopped.parentElement).not.toHaveClass('bg-red-500/10')
    expect(screen.getByText('Stopped by you. Workspace changes completed before cancellation were preserved.')).toBeInTheDocument()
    expect(screen.getByText('Available')).toBeInTheDocument()
    expect(screen.getByTitle('Send')).toBeDisabled()
  })

  it('keeps partial output but marks it durably when the owner stops a response', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    const encoder = new TextEncoder()
    let reads = 0
    let closeReader
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            reads += 1
            if (reads === 1) {
              return {
                done: false,
                value: encoder.encode('data: {"choices":[{"delta":{"content":"Partial verified work"}}]}\n\n'),
              }
            }
            return new Promise(resolve => { closeReader = resolve })
          },
          releaseLock: () => {},
        }),
      },
      headers: new Map([['content-type', 'text/event-stream']]),
    })
    globalThis.fetch.mockResolvedValueOnce(response({ aborted: true }))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'long task with partial output' },
    })
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('Partial verified work')
    fireEvent.click(screen.getByTitle('Stop'))

    expect(await screen.findByText('Response stopped')).toBeInTheDocument()
    await act(async () => { closeReader({ done: true }) })
    expect(screen.queryByText(/Completion was not confirmed/)).not.toBeInTheDocument()
    expect(screen.getByText('Response stopped')).toBeInTheDocument()
    expect(screen.getByText('Partial verified work')).toBeInTheDocument()
    expect(screen.getByText('Stopped by you. Workspace changes completed before cancellation were preserved.')).toBeInTheDocument()
    await waitFor(() => {
      const stored = JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1'))
      expect(stored.messages.at(-1).content).toContain('Stopped by you.')
      expect(stored.interrupted).toBe(false)
      expect(stored.inFlight).toBe(false)
    })
  })

  it('keeps the live stream attached and Stop retryable without an exact acknowledgement', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => new Promise(() => {}),
          releaseLock: () => {},
        }),
      },
      headers: new Map([['content-type', 'text/event-stream']]),
    })
    globalThis.fetch.mockResolvedValueOnce(response({ aborted: false }))
    globalThis.fetch.mockResolvedValueOnce(response({ aborted: true }))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'long task' },
    })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByTitle('Stop')).toBeInTheDocument())
    fireEvent.click(screen.getByTitle('Stop'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Stop was not confirmed. Pixel is still connected; retry Stop.'
    )
    expect(screen.queryByText('Response stopped')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Stop'))
    expect(await screen.findByText('Response stopped')).toBeInTheDocument()
  })

  it('enforces input limit', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    const longText = 'a'.repeat(16 * 1024 + 1)
    fireEvent.change(textarea, { target: { value: longText } })

    await waitFor(() => {
      expect(screen.getByText(/too long/)).toBeInTheDocument()
    })

    const sendBtn = screen.getByTitle('Send')
    expect(sendBtn).toBeDisabled()
  })

  it('shows only a generic message for an upstream error frame', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ error: 'upstream-secret-value' }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'test' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      expect(screen.getByText('Pixel could not complete the response.')).toBeInTheDocument()
    })
    expect(screen.queryByText(/upstream-secret-value/)).not.toBeInTheDocument()
  })

  it('marks a stream that closes without DONE as interrupted', async () => {
    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content: 'Partial answer' } }] }),
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'test' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => expect(screen.getByText('Response interrupted.')).toBeInTheDocument())
    expect(screen.getByText('Partial answer')).toBeInTheDocument()
  })

  it('renders assistant HTML as inert text', async () => {
    const maliciousFrames = [
      JSON.stringify({ choices: [{ delta: { content: '<script>alert(1)</script>' } }] }),
      '[DONE]',
    ]

    globalThis.fetch.mockResolvedValueOnce(
      response({ available: true, model: 'pixel/default', detail: 'local' })
    )
    globalThis.fetch.mockResolvedValueOnce(sseResponse(maliciousFrames))

    render(<Pixel />)

    await waitFor(() => {
      expect(screen.getByText('Available')).toBeInTheDocument()
    })

    const textarea = screen.getByPlaceholderText('Message Pixel...')
    fireEvent.change(textarea, { target: { value: 'test' } })
    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      const el = screen.getByText('<script>alert(1)</script>')
      expect(el.tagName.toLowerCase()).not.toBe('script')
    })
  })
})
