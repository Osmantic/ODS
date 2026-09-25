import { render, screen, fireEvent, act } from '@testing-library/react'
import PixelDictation from './PixelDictation'

afterEach(() => { delete window.SpeechRecognition; delete window.webkitSpeechRecognition })
function speech() {
  const instance = {start:vi.fn(),stop:vi.fn(),abort:vi.fn()}
  window.SpeechRecognition = function () { return instance }
  return instance
}
test('starts only on explicit click and inserts final dictation without sending', () => {
  const instance = speech(), insert = vi.fn()
  render(<PixelDictation onInsert={insert} conversationId="one"/>)
  expect(instance.start).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  expect(instance.start).toHaveBeenCalledOnce()
  act(() => instance.onresult({results:[Object.assign([{transcript:'Hello Pixel'}],{isFinal:true})]}))
  expect(insert).toHaveBeenCalledWith('Hello Pixel ')
  act(() => instance.onend())
  expect(screen.getByRole('button',{name:'Dictate message'})).toHaveAttribute('aria-pressed','false')
})
test('stops on conversation changes and discards late results', () => {
  const instance = speech(), insert = vi.fn()
  const view = render(<PixelDictation onInsert={insert} conversationId="one"/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  view.rerender(<PixelDictation onInsert={insert} conversationId="two"/>)
  expect(instance.abort).toHaveBeenCalledOnce()
  act(() => instance.onresult({results:[[{transcript:'late text'}]]}))
  expect(insert).not.toHaveBeenCalled()
})
test('reports unsupported browsers without changing the draft', () => {
  const insert = vi.fn()
  render(<PixelDictation onInsert={insert}/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  expect(screen.getByRole('status')).toHaveTextContent('not supported')
  expect(insert).not.toHaveBeenCalled()
})

test('Stop finishes captured speech and accepts its final transcript', () => {
  const instance = speech(), insert = vi.fn()
  render(<PixelDictation onInsert={insert} conversationId="one"/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  fireEvent.click(screen.getByRole('button',{name:'Stop dictation'}))
  expect(instance.stop).toHaveBeenCalledOnce()
  expect(instance.abort).not.toHaveBeenCalled()
  expect(screen.getByRole('status')).toHaveTextContent('Finishing dictation')
  act(() => instance.onresult({results:[Object.assign([{transcript:'Keep my last words'}],{isFinal:true})]}))
  expect(insert).toHaveBeenCalledWith('Keep my last words ')
  act(() => instance.onend())
  expect(screen.getByRole('button',{name:'Dictate message'})).toBeEnabled()
})

test('switching conversation after Stop still discards the pending transcript', () => {
  const instance = speech(), insert = vi.fn()
  const view = render(<PixelDictation onInsert={insert} conversationId="one"/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  fireEvent.click(screen.getByRole('button',{name:'Stop dictation'}))
  view.rerender(<PixelDictation onInsert={insert} conversationId="two"/>)
  act(() => instance.onresult({results:[Object.assign([{transcript:'Old conversation'}],{isFinal:true})]}))
  expect(insert).not.toHaveBeenCalled()
  expect(instance.abort).toHaveBeenCalledOnce()
  expect(screen.getByRole('button',{name:'Dictate message'})).toBeEnabled()
})
test('reports denied permission and aborts recognition on unmount', () => {
  const instance = speech()
  const view = render(<PixelDictation onInsert={() => {}}/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  act(() => instance.onerror({error:'not-allowed'}))
  expect(screen.getByRole('status')).toHaveTextContent('not granted')
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  view.unmount()
  expect(instance.abort).toHaveBeenCalledOnce()
})

test('appends only newly finalized results', () => {
  const instance=speech(),insert=vi.fn()
  render(<PixelDictation onInsert={insert}/>)
  fireEvent.click(screen.getByRole('button',{name:'Dictate message'}))
  const first=Object.assign([{transcript:'First'}],{isFinal:true})
  const second=Object.assign([{transcript:'Second'}],{isFinal:true})
  act(() => instance.onresult({resultIndex:0,results:[first]}))
  act(() => instance.onresult({resultIndex:1,results:[first,second]}))
  expect(insert.mock.calls).toEqual([['First '],['Second ']])
})
