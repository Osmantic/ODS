import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import ODSTalk from './ODSTalk'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(url => url === '/api/talk/status'
    ? Promise.resolve({ok:true,json:async () => ({capabilities:{text_chat:true}})})
    : new Promise(() => {})))
})
afterEach(() => vi.unstubAllGlobals())
it.each(['Enter', 'button'])('sends the selected file and caption using %s', async method => {
  render(<ODSTalk/>); await screen.findByText('Ready')
  const file = new File(['original bytes'], 'notes.txt', {type:'text/plain'})
  fireEvent.change(document.querySelector('input[type=file]'), {target:{files:[file]}})
  const field=screen.getByPlaceholderText('Message ODS')
  fireEvent.change(field, {target:{value:'Summarize'}})
  if(method==='Enter') fireEvent.keyDown(field,{key:'Enter'})
  else fireEvent.click(screen.getByRole('button',{name:'Send message'}))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/talk/attachment',expect.any(Object)))
  const options=fetch.mock.calls.find(([url]) => url==='/api/talk/attachment')[1]
  expect(options.body.get('text')).toBe('Summarize')
  expect(options.body.get('file').name).toBe('notes.txt')
  expect(fetch.mock.calls.some(([url]) => url==='/api/talk/message/stream')).toBe(false)
})
it('keeps IME confirmation and Shift+Enter in the composer', async () => {
  render(<ODSTalk/>);await screen.findByText('Ready')
  const field=screen.getByPlaceholderText('Message ODS')
  fireEvent.change(field,{target:{value:'composition draft'}})
  fireEvent.keyDown(field,{key:'Enter',isComposing:true})
  fireEvent.keyDown(field,{key:'Enter',shiftKey:true})
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(field).toHaveValue('composition draft')
})
