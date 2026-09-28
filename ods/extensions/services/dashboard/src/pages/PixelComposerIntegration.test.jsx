import {act, fireEvent, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({available:true,model:'pixel/default'})})))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})

it('keeps one file input and draft preview across composer updates and chat switches', async () => {
  render(<Pixel/>)
  await screen.findByText('Available')
  for (const value of ['One', 'Two\nlines', 'Three', '']) {
    fireEvent.change(screen.getByPlaceholderText('Message Portal...'), {target:{value}})
    expect(screen.getAllByRole('button', {name:'Add text file',exact:true})).toHaveLength(1)
    expect(screen.getAllByRole('button', {name:'Preview draft',exact:true})).toHaveLength(1)
  }
  fireEvent(window, new Event('ods:pixel-new-task'))
  expect(screen.getAllByRole('button', {name:'Add text file',exact:true})).toHaveLength(1)
})

function pendingTurn() {
  let finish
  const firstChunk = new Promise(resolve => { finish = resolve })
  let started = false
  return {
    response: {ok:true, headers:new Map([['content-type','text/event-stream']]), body:{getReader:() => ({
      read:() => {
        if (started) return Promise.resolve({done:true})
        started = true
        return firstChunk
      },
      releaseLock:() => {},
    })}},
    finish:() => finish({done:false,value:new TextEncoder().encode(
      'data: {"choices":[{"delta":{"content":"Verified reply"}}]}\n\ndata: [DONE]\n\n',
    )}),
  }
}

it.each(['keyboard', 'mouse'])('allows repeated type/send turns via %s without refocusing manually', async method => {
  const user = userEvent.setup()
  const turns = [pendingTurn(), pendingTurn()]
  let turnIndex = 0
  fetch.mockImplementation(async url => url === '/api/pixel/chat/stream' ? turns[turnIndex++].response
    : {ok:true,json:async () => ({available:true,model:'pixel/default'})})
  render(<Pixel/>)
  const field = await screen.findByPlaceholderText('Message Portal...')
  await waitFor(() => expect(field).toHaveFocus())
  for (let index = 0; index < turns.length; index++) {
    await user.keyboard(`Question ${index + 1}`)
    if (method === 'keyboard') await user.keyboard('{Enter}')
    else await user.click(screen.getByRole('button', {name:'Send',exact:true}))
    await waitFor(() => expect(field).toBeDisabled())
    // jsdom keeps a disabled element focused; reproduce the browser's blur.
    field.disabled = false
    field.blur()
    field.disabled = true
    expect(field).not.toHaveFocus()
    await act(async () => { turns[index].finish() })
    await waitFor(() => expect(field).toBeEnabled())
    expect(field).toHaveFocus()
    expect(field).toHaveValue('')
  }
  const requests = fetch.mock.calls.filter(([url]) => url === '/api/pixel/chat/stream')
  expect(requests).toHaveLength(2)
  expect(JSON.parse(requests[1][1].body).messages.at(-1).content).toBe('Question 2')
})

it('keeps a search field focused when a response finishes', async () => {
  const user = userEvent.setup()
  const turn = pendingTurn()
  fetch.mockImplementation(async url => url === '/api/pixel/chat/stream' ? turn.response
    : {ok:true,json:async () => ({available:true,model:'pixel/default'})})
  render(<><input aria-label="Workspace search"/><Pixel/></>)
  const field = await screen.findByPlaceholderText('Message Portal...')
  await user.keyboard('Question{Enter}')
  await waitFor(() => expect(field).toBeDisabled())
  const search = screen.getByRole('textbox', {name:'Workspace search'})
  await user.type(search, 'my file')
  await act(async () => { turn.finish() })
  await waitFor(() => expect(field).toBeEnabled())
  expect(search).toHaveFocus()
  expect(search).toHaveValue('my file')
})

it('restores composer focus when a request fails so the next message can be typed', async () => {
  const user = userEvent.setup()
  let reject
  const request = new Promise((_, rejectRequest) => { reject = rejectRequest })
  fetch.mockImplementation(async url => url === '/api/pixel/chat/stream' ? request
    : {ok:true,json:async () => ({available:true,model:'pixel/default'})})
  render(<Pixel/>)
  const field = await screen.findByPlaceholderText('Message Portal...')
  await user.keyboard('Question{Enter}')
  await waitFor(() => expect(field).toBeDisabled())
  field.disabled = false
  field.blur()
  field.disabled = true
  await act(async () => { reject(new Error('Network unavailable')) })
  await waitFor(() => expect(field).toBeEnabled())
  expect(field).toHaveFocus()
  await user.keyboard('Try again')
  expect(field).toHaveValue('Try again')
})
