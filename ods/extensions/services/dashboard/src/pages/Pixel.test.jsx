import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '../test/test-utils'
import { act } from '@testing-library/react'
import {saveProfile} from '../lib/localProfile'
import {saveConversation,readConversations,DELETE_EVENT} from '../lib/pixelConversations'

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
    expect(frame).toHaveAttribute('src', `/pixel-preview/${siteId}/__ods_view__.html`)
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-forms allow-downloads')
    expect(screen.queryByLabelText('Snapshot details')).not.toBeInTheDocument()
    expect(screen.queryByText('Info', {exact:true})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name:'Files',exact:true})).toBeInTheDocument()
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
    expect(await screen.findByTitle('Interactive Pixel preview')).toHaveAttribute('src', `/pixel-preview/${siteId}/__ods_view__.html`)
    expect(globalThis.fetch.mock.calls.filter(([url]) => url === '/api/pixel/chat/stream')).toHaveLength(1)

    fireEvent.click(screen.getByTitle('Close preview'))
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
    await waitFor(() => expect(
      JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1')).workspaceOpen
    ).toBe(false))
    expect(JSON.parse(globalThis.localStorage.getItem('ods.pixel.chat.v1')).preview).toEqual(preview)
    restored.unmount()
    render(<Pixel />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.queryByTitle('Interactive Pixel preview')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the verified preview' })).toHaveAttribute('href', `/pixel-preview/${siteId}/`)
    fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
    expect(screen.getByTitle('Interactive Pixel preview')).toHaveAttribute('src', `/pixel-preview/${siteId}/__ods_view__.html`)
  })

  it('opens the workspace without a preview and only drafts a publication request', async () => {
    globalThis.fetch.mockResolvedValue(response({available:true}))
    render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
    expect(screen.getByText('No preview published yet')).toBeVisible()
    expect(screen.queryByTitle('Interactive Pixel preview')).toBeNull()
    fireEvent.click(screen.getByRole('button',{name:'Ask Pixel to publish'}))
    expect(screen.getByPlaceholderText('Message Pixel...').value).toContain('pixel_ods_workspace_preview')
    expect(globalThis.fetch.mock.calls.some(([url]) => url === '/api/pixel/chat/stream')).toBe(false)
    fireEvent.click(screen.getByTitle('Close preview'))
    expect(screen.queryByText('No preview published yet')).toBeNull()
    fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
    expect(screen.getByText('No preview published yet')).toBeVisible()
  })

  it('deletes the selected idle chat and starts an empty one without resurrecting it', async () => {
    saveConversation({schema:1,chatId:'delete-current',messages:[{role:'user',content:'Disposable current chat'}]})
    globalThis.fetch.mockResolvedValue(response({available:true,model:'pixel/default'}))
    render(<Pixel/>)
    await waitFor(()=>expect(screen.getByText('Available')).toBeInTheDocument())
    expect(screen.getByText('Disposable current chat')).toBeVisible()
    const complete=vi.fn()
    act(()=>window.dispatchEvent(new CustomEvent(DELETE_EVENT,{detail:{chatId:'delete-current',complete}})))
    expect(complete).toHaveBeenCalledWith('')
    await waitFor(()=>expect(screen.queryByText('Disposable current chat')).not.toBeInTheDocument())
    expect(readConversations().some(chat=>chat.chatId==='delete-current')).toBe(false)
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).chatId).not.toBe('delete-current')
  })

  it('shows the local profile photo beside user messages without including it in model requests',async()=>{
    const photo='data:image/webp;base64,YWJj'
    saveProfile({name:'Gabriel',photo})
    globalThis.fetch.mockResolvedValueOnce(response({available:true}))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([JSON.stringify({choices:[{delta:{content:'Hello'}}]}),'[DONE]']))
    render(<Pixel/>)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Hi'}})
    fireEvent.click(screen.getByTitle('Send'))
    expect(await screen.findByRole('img',{name:'Gabriel profile photo'})).toHaveAttribute('src',photo)
    expect(screen.getByRole('img',{name:'Gabriel profile photo'}).parentElement).toHaveClass('pixel-user-character')
    const post=globalThis.fetch.mock.calls.find(([,options])=>options?.method==='POST')
    expect(post[1].body).not.toContain(photo)
    await act(async()=>saveProfile({name:'Gabriel',photo:''}))
    expect(screen.queryByRole('img',{name:'Gabriel profile photo'})).toBeNull()
    expect(screen.getByRole('img',{name:'Gabriel avatar'})).toBeVisible()
  })

  it('keeps the chat toolbar inside its column beside the full-height workspace', async () => {
    globalThis.fetch.mockResolvedValue(response({available:true,model:'pixel/default'}))
    render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
    const panel = screen.getByRole('complementary',{name:'Preview panel'})
    const column = screen.getByPlaceholderText('Message Pixel...').closest('.pixel-chat-column')
    expect(column.parentElement).toBe(panel.parentElement)
    expect(column).toContainElement(screen.getByRole('button',{name:'Workspace',exact:true}))
    expect(column).toContainElement(screen.getByRole('link',{name:'Change model'}))
    expect(column).toContainElement(screen.getByRole('button',{name:'Search Pixel'}))
    expect(panel).not.toContainElement(screen.getByRole('heading',{name:'Pixel',exact:true}))
    fireEvent.click(screen.getByTitle('Collapse preview'))
    expect(panel).toHaveClass('is-collapsed')
    fireEvent.click(screen.getByTitle('Expand preview'))
    expect(panel).not.toHaveClass('is-collapsed')
  })

  it('saves terminal activity with the reply and never sends telemetry to the model', async () => {
    const task={schemaVersion:1,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:'2026-09-08T20:00:00.000Z',finishedAt:'2026-09-08T20:00:02.000Z',state:'completed',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'read',calls:1,failures:0,blocked:0}]}
    globalThis.fetch.mockResolvedValueOnce(response({available:true}))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({choices:[{delta:{content:'Read the file.'}}]}),
      JSON.stringify({id:task.runId,pixel_task:task,choices:[{delta:{},finish_reason:'stop'}]}),'[DONE]']))
    const first=render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Read the file'}})
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(()=>expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).task).toEqual(task))
    first.unmount()
    globalThis.fetch.mockResolvedValueOnce(response({available:true}))
    render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
    fireEvent.click(screen.getByRole('button',{name:'Activity',exact:true}))
    expect(screen.getByText('Read')).toBeVisible()
    globalThis.fetch.mockResolvedValueOnce(sseResponse([JSON.stringify({choices:[{delta:{content:'OK'}}]}),'[DONE]']))
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Continue'}})
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('OK')
    const calls=globalThis.fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')
    expect(JSON.parse(calls.at(-1)[1].body).messages.every(message=>Object.keys(message).sort().join(',')==='content,role')).toBe(true)
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
    const frame = await screen.findByTitle('Interactive Pixel preview')
    expect(frame).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Collapse preview'))
    expect(frame).not.toBeVisible()
    expect(screen.getByTitle('Interactive Pixel preview')).toBe(frame)
    fireEvent.click(screen.getByTitle('Expand preview'))
    expect(frame).toBeVisible()
    expect(screen.getByTitle('Interactive Pixel preview')).toBe(frame)
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
      frameUrl: '/pixel-preview/site-0123456789abcdef01234567/__ods_view__.html',
      sandbox: 'allow-scripts allow-forms allow-downloads',
      route: 'private-dashboard',
    })
    for (const hostname of ['localhost', '127.0.0.1', '[::1]', '::1']) {
      expect(resolvePreviewAccess(preview, { hostname, protocol: 'http:' })).toEqual({
        url: `/pixel-preview/${preview.siteId}/`,
        frameUrl: `/pixel-preview/${preview.siteId}/__ods_view__.html`,
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

  it('keeps dictation beside send and distinguishes characters from model context', async () => {
    globalThis.fetch.mockResolvedValue(response({ available: true, model: 'pixel/default' }))
    render(<Pixel systemStatus={{ inference: { loadedModel: 'local-model', contextSize: 65536 } }} />)
    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument())
    const send = screen.getByTitle('Send')
    expect(send.parentElement).toContainElement(screen.getByRole('button', { name: 'Dictate message' }))
    expect(send.parentElement).toHaveClass('pixel-composer-actions')
    expect(screen.getByText('64K context')).toHaveAttribute('title', expect.stringContaining('Model context'))
    const counter = screen.getByTitle('Characters in this message, not tokens or context usage')
    expect(counter).toHaveTextContent(`0 / ${(16 * 1024).toLocaleString()} chars`)
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'Olá' } })
    expect(counter).toHaveTextContent(`3 / ${(16 * 1024).toLocaleString()} chars`)
    expect(screen.getByText('64K context')).toBeInTheDocument()
    const field = screen.getByPlaceholderText('Message Pixel...')
    expect(field).toHaveClass('pixel-composer-input')
    expect(field.className).not.toContain('focus:ring')
    fireEvent.keyDown(field,{key:'Enter',shiftKey:true})
    fireEvent.change(field,{target:{value:'Olá\nsegunda linha\nterceira linha'}})
    expect(field).toHaveValue('Olá\nsegunda linha\nterceira linha')
    expect(send.parentElement.parentElement).toHaveClass('pixel-composer-row')
    expect(send.parentElement).toContainElement(screen.getByRole('button',{name:'Dictate message'}))
    expect(globalThis.fetch.mock.calls.every(([,options]) => options?.method !== 'POST')).toBe(true)
  })

  it('highlights fenced code while keeping unknown languages and HTML inert', async () => {
    const content = '```js\nconst value = "Pixel";\n```\n\n```unknown-language\n<video onerror="alert(1)">\n```'
    globalThis.fetch.mockResolvedValueOnce(response({ available: true }))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({ choices: [{ delta: { content } }] }), '[DONE]',
    ]))
    const { container } = render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), { target: { value: 'Show code' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(container.querySelector('.hljs-keyword')).toHaveTextContent('const'))
    expect(container.querySelector('.hljs-string')).toHaveTextContent('"Pixel"')
    expect(container.querySelector('.language-unknown-language')).toHaveTextContent('<video onerror="alert(1)">')
    expect(container.querySelector('video')).toBeNull()
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

  it('preserves earlier file changes across another message and reload without sending UI metadata to the model', async () => {
    const publication = letter => {
      const sha256 = letter.repeat(64)
      const siteId = `site-${sha256.slice(0, 24)}`
      return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', relativeDirectory: 'demo', siteId, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, files: 1, bytes: 100, sha256, entrySha256: sha256}
    }
    const before = publication('a')
    const after = publication('b')
    const changes = {schemaVersion: 1, scope: 'published-snapshots', siteId: after.siteId, sha256: after.sha256, beforeSiteId: before.siteId, beforeSha256: before.sha256, changes: [{path: 'index.html', change: 'modified', additions: 22, deletions: 9, truncated: true, diff: []}]}
    globalThis.localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema: 1, chatId: 'diff-history', messages: [
      {role: 'user', content: 'edit the game'},
      {role: 'assistant', content: 'Game updated', publication: after, beforePublication: before},
    ]}))
    globalThis.fetch.mockImplementation(async (url) => {
      if (url.includes('__ods_changes__')) return {ok: true, headers: new Map(), arrayBuffer: async () => new TextEncoder().encode(JSON.stringify(changes)).buffer}
      if (url === '/api/pixel/chat/stream') return sseResponse([JSON.stringify({choices: [{delta: {content: 'Second answer'}}]}), '[DONE]'])
      return response({available: true, model: 'pixel/default'})
    })
    const view = render(<Pixel />)
    expect(await screen.findByText('Edited index.html')).toBeVisible()
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {target: {value: 'another message'}})
    fireEvent.click(screen.getByTitle('Send'))
    expect(screen.getByText('Edited index.html')).toBeVisible()
    expect(await screen.findByText('Second answer')).toBeVisible()
    expect(screen.getByText('Edited index.html')).toBeVisible()
    expect(screen.getAllByLabelText('22 lines added, 9 lines removed')).toHaveLength(2)
    const call = globalThis.fetch.mock.calls.find(([url]) => url === '/api/pixel/chat/stream')
    expect(JSON.parse(call[1].body).messages).toEqual([
      {role: 'user', content: 'edit the game'}, {role: 'assistant', content: 'Game updated'}, {role: 'user', content: 'another message'},
    ])
    const stored = JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
    expect(stored.messages[1]).toMatchObject({publication: after, beforePublication: before})
    view.unmount()
    render(<Pixel />)
    expect(await screen.findByText('Edited index.html')).toBeVisible()
    expect(screen.getByText('Second answer')).toBeVisible()
  })

  it('keeps visible history beyond the model request limit after send and reload', async () => {
    const messages = Array.from({length: 60}, (_, index) => ({role: index % 2 ? 'assistant' : 'user', content: `History item ${index}`}))
    localStorage.setItem('ods.pixel.chat.v1', JSON.stringify({schema: 1, chatId: 'long-history', messages}))
    globalThis.fetch.mockImplementation(async url => url === '/api/pixel/chat/stream'
      ? sseResponse([JSON.stringify({choices: [{delta: {content: 'Latest answer'}}]}), '[DONE]'])
      : response({available: true, model: 'pixel/default'}))
    const view = render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {target: {value: 'continue'}})
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('Latest answer')
    expect(screen.getByText('History item 0')).toBeVisible()
    const call = globalThis.fetch.mock.calls.find(([url]) => url === '/api/pixel/chat/stream')
    expect(JSON.parse(call[1].body).messages.length).toBeLessThanOrEqual(50)
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages).toHaveLength(62)
    view.unmount()
    render(<Pixel />)
    expect(screen.getByText('History item 0')).toBeVisible()
    expect(screen.getByText('Latest answer')).toBeVisible()
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
    expect(screen.getByText('What do you want to work on?')).toBeInTheDocument()
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
    const savedRecovery = JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
    expect(savedRecovery.contextStart).toBe(2)
    expect(savedRecovery.messages[0].content).toBe('old context')
    expect(screen.getByText('old answer')).toBeVisible()

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
    expect(screen.getByText('What do you want to work on?')).toBeInTheDocument()

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

  it('rechecks the selected interrupted chat even when the previous interrupted chat is terminal', async () => {
    saveConversation({schema:1,chatId:'second_interrupted',interrupted:true,messages:[{role:'user',content:'Another task'}]})
    saveInterruptedChat()
    globalThis.fetch.mockImplementation(async (url, options) => {
      if (url === '/api/pixel/status') return response({available:true})
      expect(url).toBe('/api/pixel/chat/activity')
      const {chat_id} = JSON.parse(options.body)
      return response({state:chat_id === 'second_interrupted' ? 'active' : 'terminal'})
    })
    render(<Pixel />)
    await screen.findByText(/previous request is no longer active/)
    act(()=>window.dispatchEvent(new CustomEvent('ods:pixel-select-conversation',{detail:'second_interrupted'})))
    await screen.findByText('Working in this chat')
    expect(fetch).toHaveBeenCalledWith('/api/pixel/chat/activity',expect.objectContaining({body:JSON.stringify({chat_id:'second_interrupted'})}))
    expect(screen.getByPlaceholderText('Message Pixel...')).toBeDisabled()
    expect(screen.getByTitle('Stop')).toBeEnabled()
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
      const reply = screen.getByText('Starting the owner-agent turn').closest('[data-pixel-response]')
      expect(reply.querySelectorAll('.pixel-character')).toHaveLength(1)
      expect(reply.querySelector('.pixel-reply-character')).not.toBeNull()
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

  it.each(['delta', 'error', 'eof'])('isolates a new reply from a stopped reader that delivers a late %s', async late => {
    const pending = []
    let requests = 0
    globalThis.fetch.mockImplementation(async url => {
      if (url === '/api/pixel/status') return response({available:true})
      if (url === '/api/pixel/chat/cancel') return response({aborted:true})
      if (url !== '/api/pixel/chat/stream') throw new Error('Unexpected request')
      const index = requests++
      return {ok:true, status:200, body:{getReader:()=>({
        read:()=>new Promise((resolve,reject)=>{pending[index]={resolve,reject}}),
        releaseLock:()=>{},
      })}}
    })
    render(<Pixel />)
    await screen.findByText('Available')
    const send = text => {
      fireEvent.change(screen.getByPlaceholderText('Message Pixel...'), {target:{value:text}})
      fireEvent.click(screen.getByTitle('Send'))
    }
    send('First task')
    await waitFor(()=>expect(pending[0]).toBeDefined())
    fireEvent.click(screen.getByTitle('Stop'))
    await screen.findByText('Response stopped')
    send('Second task')
    await waitFor(()=>expect(pending[1]).toBeDefined())
    await act(async()=>{
      if (late === 'error') pending[0].reject(new Error('Late network failure'))
      else pending[0].resolve(late === 'eof' ? {done:true} : {
        done:false,value:new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Old reply leaking"}}]}\n\ndata: [DONE]\n\n'),
      })
    })
    expect(screen.queryByText('Old reply leaking')).not.toBeInTheDocument()
    expect(screen.getByTitle('Stop')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).inFlight).toBe(true)
    await act(async()=>pending[1].resolve({done:false,value:new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Second reply correct"}}]}\n\ndata: [DONE]\n\n')}))
    await screen.findByText('Second reply correct')
    expect(screen.getByText('Response stopped')).toBeInTheDocument()
    expect(screen.getByText('Available')).toBeInTheDocument()
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

  it('preserves partial answers and ignores late content after an upstream error', async () => {
    globalThis.fetch.mockResolvedValueOnce(response({available:true}))
    globalThis.fetch.mockResolvedValueOnce(sseResponse([
      JSON.stringify({choices:[{delta:{content:'Work already explained'}}]}),
      JSON.stringify({error:'private-upstream-error'}),
      JSON.stringify({choices:[{delta:{content:'False late success'}}]}),
      '[DONE]',
    ]))
    const view=render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'test'}})
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('Pixel could not complete the response.')
    expect(screen.getByText('Work already explained')).toBeInTheDocument()
    expect(screen.queryByText(/False late success|private-upstream-error/)).toBeNull()
    await waitFor(()=>{
      const saved=JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
      expect(saved.messages.at(-1).status).toBe('error')
      expect(saved.inFlight).toBe(false)
    })
    view.unmount()
    globalThis.fetch.mockResolvedValue(response({available:true}))
    render(<Pixel />)
    expect(screen.getByText('Work already explained')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages.at(-1).status).toBe('error')
  })

  it('restores long assistant replies without losing the chat and bounds only their API copy',async()=>{
    const longReply='Long explanation '+ 'x'.repeat(20000)
    localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'long_reply',messages:[{role:'user',content:'Original task'},{role:'assistant',content:longReply,status:'done'}]}))
    globalThis.fetch.mockImplementation(async url=>url==='/api/pixel/chat/stream' ? sseResponse([JSON.stringify({choices:[{delta:{content:'Follow-up answer'}}]}),'[DONE]']) : response({available:true}))
    render(<Pixel />)
    await screen.findByText('Available')
    expect(screen.getByText(longReply)).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Message Pixel...'),{target:{value:'Continue'}})
    fireEvent.click(screen.getByTitle('Send'))
    await screen.findByText('Follow-up answer')
    const body=JSON.parse(fetch.mock.calls.find(([url])=>url==='/api/pixel/chat/stream')[1].body)
    expect(body.chat_id).toBe('long_reply')
    expect(body.messages.every(message=>message.content.length<=16384)).toBe(true)
    expect(body.messages[1].content).toContain('shortened for model context')
    expect(screen.getByText(longReply)).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).messages[1].content).toBe(longReply)
  })

  it.each(['Files','Changes'])('reloads the active %s inspector after a failed fetch',async tab=>{
    const sha256='a'.repeat(64),siteId=`site-${sha256.slice(0,24)}`
    localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'reload_inspector',messages:[{role:'user',content:'Inspect project'}],preview:{schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',siteId,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,files:1,bytes:100,sha256,entrySha256:'b'.repeat(64)}}))
    globalThis.fetch.mockImplementation(async url=>url==='/api/pixel/status' ? response({available:true}) : {ok:false})
    render(<Pixel />)
    await screen.findByText('Available')
    fireEvent.click(screen.getByRole('button',{name:tab,exact:true}))
    await screen.findByText(tab==='Files' ? 'Task files could not be verified.' : /File comparison unavailable/)
    const requests=()=>fetch.mock.calls.filter(([url])=>url.startsWith('/pixel-preview/')).length
    const before=requests()
    fireEvent.click(screen.getByTitle('Reload preview'))
    await waitFor(()=>expect(requests()).toBe(before+1))
    expect(screen.getByRole('button',{name:tab,exact:true})).toHaveAttribute('aria-pressed','true')
    expect(screen.getAllByTitle('Interactive Pixel preview')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button',{name:'Preview',exact:true}))
    expect(screen.getAllByTitle('Interactive Pixel preview')).toHaveLength(1)
    expect(screen.queryByRole('region',{name:'Task files'})).not.toBeInTheDocument()
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
