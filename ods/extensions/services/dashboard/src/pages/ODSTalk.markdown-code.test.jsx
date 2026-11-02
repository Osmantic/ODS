import { fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import ODSTalk from './ODSTalk' // eslint-disable-line no-unused-vars

const reply = 'Use `ods doctor` before retry.\n\n```sh\nods doctor\nods status\n```\n\n```\nplain block\nsecond line\n```'

afterEach(() => vi.unstubAllGlobals())

test('Talk keeps code spans in prose and preserves fenced code with and without a language', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/talk/status') return { ok: true, status: 200, json: async () => ({ capabilities: { text_chat: true } }) }
    const frames = [{ type: 'complete', text: reply, status: 'ok' }, { type: 'done' }]
    let sent = false
    return { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
      if (sent) return { done: true }
      sent = true
      return { done: false, value: new TextEncoder().encode(frames.map(frame => `data: ${JSON.stringify(frame)}\n\n`).join('')) }
    }, cancel: async () => {} }) } }
  }))
  const { container } = render(<ODSTalk />)
  await screen.findByText('Ready')
  fireEvent.change(screen.getByPlaceholderText('Message ODS'), { target: { value: 'How do I diagnose this?' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  const prose = await screen.findByText('ods doctor', { selector: 'p > code' })
  expect(prose.parentElement).toHaveTextContent('Use ods doctor before retry.')
  expect(prose).not.toHaveClass('block')
  const fences = container.querySelectorAll('pre > code')
  expect(fences).toHaveLength(2)
  expect(fences[0]).toHaveClass('language-sh')
  expect(fences[0].textContent).toBe('ods doctor\nods status\n')
  expect(fences[1].textContent).toBe('plain block\nsecond line\n')
})
