import { act, fireEvent, render, screen } from '@testing-library/react'
import PixelDictation from './PixelDictation'

afterEach(() => { delete window.SpeechRecognition })

test('cancels a pending speech finalization, preserves received text and rejects late results', () => {
  const sessions = []
  window.SpeechRecognition = function () {
    const session = { start: vi.fn(), stop: vi.fn(), abort: vi.fn() }
    sessions.push(session)
    return session
  }
  const onInsert = vi.fn()
  render(<PixelDictation conversationId="one" onInsert={onInsert} />)
  fireEvent.click(screen.getByRole('button', { name: 'Dictate message' }))
  const first = sessions[0]
  act(() => first.onresult({ results: [[{ transcript: 'Already received' }]] }))
  fireEvent.click(screen.getByRole('button', { name: 'Stop dictation' }))
  expect(first.stop).toHaveBeenCalledOnce()
  expect(first.abort).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel dictation' }))
  expect(first.abort).toHaveBeenCalledOnce()
  expect(screen.getByRole('status')).toHaveTextContent('Text already received is kept')
  fireEvent.click(screen.getByRole('button', { name: 'Dictate message' }))
  act(() => { first.onresult({ results: [[{ transcript: 'Late old words' }]] }); first.onend() })
  expect(screen.getByRole('button', { name: 'Stop dictation' })).toHaveAttribute('aria-pressed', 'true')
  act(() => sessions[1].onresult({ results: [[{ transcript: 'New words' }]] }))
  expect(onInsert.mock.calls).toEqual([['Already received '], ['New words ']])
})
