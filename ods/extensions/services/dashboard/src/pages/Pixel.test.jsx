import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import { act } from '@testing-library/react'

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

  it('opens an interactive side panel only from a verified terminal marker', async () => {
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
      JSON.stringify({ choices: [{ delta: { content: `Preview: ${preview.url}` } }] }),
      JSON.stringify({
        choices: [{ delta: {}, finish_reason: 'stop' }],
        pixel: { schemaVersion: 1, preview },
      }),
      '[DONE]',
    ]))

    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {
      target: { value: 'Build and show me a demo website.' },
    })
    fireEvent.click(screen.getByTitle('Send'))

    const frame = await screen.findByTitle('Interactive Pixel preview')
    expect(frame).toHaveAttribute('src', preview.url)
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin')
    expect(screen.getByText('Host verified · 3 files')).toBeInTheDocument()
    expect(screen.getByTitle('Open preview in a new tab')).toHaveAttribute('href', preview.url)

    fireEvent.click(screen.getByTitle('Close preview'))
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
  })

  it('uses the authenticated Dashboard preview route with an opaque sandbox remotely', () => {
    const preview = {
      siteId: 'site-0123456789abcdef01234567',
      url: 'http://site-0123456789abcdef01234567.localhost:9437/site-0123456789abcdef01234567/',
    }
    expect(resolvePreviewAccess(preview, {
      hostname: 'dashboard.ods.local',
      protocol: 'http:',
    })).toEqual({
      url: '/pixel-preview/site-0123456789abcdef01234567/',
      sandbox: 'allow-scripts',
      route: 'private-dashboard',
    })
    expect(resolvePreviewAccess(preview, {
      hostname: 'localhost',
      protocol: 'http:',
    })).toEqual({
      url: preview.url,
      sandbox: 'allow-scripts allow-same-origin',
      route: 'loopback',
    })
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
            return new Promise(() => {})
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
    expect(screen.getByText('Partial verified work')).toBeInTheDocument()
    expect(screen.getByText('Stopped by you. Workspace changes completed before cancellation were preserved.')).toBeInTheDocument()
    await waitFor(() => {
      const stored = JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1'))
      expect(stored.messages.at(-1).content).toContain('Stopped by you.')
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
