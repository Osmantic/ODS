import { act, fireEvent, render, screen } from '@testing-library/react'
import TalkExport from './TalkExport' // eslint-disable-line no-unused-vars

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('exports only completed tab text, excludes attachment payloads, and releases the download URL', async () => {
  vi.useFakeTimers()
  const create = vi.fn(() => 'blob:export')
  const revoke = vi.fn()
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
  const click = vi.spyOn(window.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const messages = [
    { id: 'welcome', role: 'assistant', text: 'welcome' },
    { id: 'user', role: 'user', text: 'Xin chào', imageUrl: 'data:image/png;base64,private', attachmentForRetry: { file: 'private payload' } },
    { id: 'reply', role: 'assistant', text: 'Interrupted', status: 'error' },
  ]
  const view = render(<TalkExport messages={messages} busy />)
  fireEvent.click(screen.getByRole('button', { name: 'Export conversation' }))
  expect(create).not.toHaveBeenCalled()
  view.rerender(<TalkExport messages={messages} busy={false} />)
  fireEvent.change(screen.getByLabelText('Transcript format'), { target: { value: 'json' } })
  fireEvent.click(screen.getByRole('button', { name: 'Export conversation' }))
  expect(click).toHaveBeenCalledTimes(1)
  expect(click.mock.instances[0].download).toMatch(/^ods-talk-\d{4}-\d{2}-\d{2}\.json$/)
  expect(document.querySelector('a[download]')).toBeNull()
  const blob = create.mock.calls[0][0]
  expect(revoke).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(revoke).toHaveBeenCalledWith('blob:export')
  vi.useRealTimers()
  const content = await new Promise(resolve => {
    const reader = new window.FileReader()
    reader.onload = () => resolve(reader.result)
    reader.readAsText(blob)
  })
  expect(JSON.parse(content).messages).toEqual([
    { role: 'user', text: 'Xin chào', status: 'done' },
    { role: 'assistant', text: 'Interrupted', status: 'error' },
  ])
  expect(content).not.toContain('private')
})
