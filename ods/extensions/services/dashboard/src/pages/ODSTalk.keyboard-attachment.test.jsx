import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const ready = { ok: true, status: 200, json: async () => ({ capabilities: { text_chat: true } }) }
const reply = () => ({ ok: true, status: 200, body: new globalThis.ReadableStream({ start(controller) {
  controller.enqueue(new TextEncoder().encode('data: {"type":"complete","text":"Attachment received","status":"ok"}\n\ndata: {"type":"done"}\n\n'))
  controller.close()
} }) })

describe('Talk keyboard attachment submission', () => {
  afterEach(() => vi.unstubAllGlobals())

  test.each([
    ['image without caption', 'image/png', 'photo.png', ''],
    ['text with caption', 'text/plain', 'notes.txt', 'Summarize these notes'],
  ])('Enter sends %s through the attachment endpoint', async (_label, type, name, caption) => {
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:talk-test', revokeObjectURL: vi.fn() })
    const fetchMock = vi.fn(async url => url === '/api/talk/status' ? ready : reply())
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<ODSTalk />)
    await screen.findByText('Ready')
    const file = new File(['sample'], name, { type })
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [file] } })
    const composer = screen.getByPlaceholderText('Message ODS')
    fireEvent.change(composer, { target: { value: caption } })
    fireEvent.keyDown(composer, { key: 'Enter', code: 'Enter' })
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url !== '/api/talk/status')).toHaveLength(1))
    const [url, options] = fetchMock.mock.calls.find(([url]) => url !== '/api/talk/status')
    expect(url).toBe('/api/talk/attachment')
    expect(options.body.get('file').name).toBe(name)
    expect(options.body.get('text')).toBe(caption)
    expect(await screen.findByText('Attachment received')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove attachment' })).not.toBeInTheDocument()
  })

  test('Shift+Enter keeps the attachment and does not send', async () => {
    const fetchMock = vi.fn(async () => ready)
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<ODSTalk />)
    await screen.findByText('Ready')
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(['notes'], 'notes.txt', { type: 'text/plain' })] },
    })
    fireEvent.keyDown(screen.getByPlaceholderText('Message ODS'), { key: 'Enter', shiftKey: true })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeInTheDocument()
  })

  test.each([
    ['composition flag', { isComposing: true }],
    ['Safari composition key', { keyCode: 229 }],
  ])('IME confirmation with %s preserves the draft and attachment', async (_label, composition) => {
    const fetchMock = vi.fn(async url => url === '/api/talk/status' ? ready : reply())
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<ODSTalk />)
    await screen.findByText('Ready')
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(['notes'], 'notes.txt', { type: 'text/plain' })] },
    })
    const composer = screen.getByPlaceholderText('Message ODS')
    fireEvent.change(composer, { target: { value: '日本語' } })
    expect(fireEvent.keyDown(composer, { key: 'Enter', code: 'Enter', ...composition })).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(composer).toHaveValue('日本語')
    expect(screen.getByRole('button', { name: 'Remove attachment' })).toBeInTheDocument()
    fireEvent.keyDown(composer, { key: 'Enter', code: 'Enter' })
    await screen.findByText('Attachment received')
    const [url, options] = fetchMock.mock.calls.find(([request]) => request !== '/api/talk/status')
    expect(url).toBe('/api/talk/attachment')
    expect(options.body.get('text')).toBe('日本語')
    expect(options.body.get('file').name).toBe('notes.txt')
  })
})
