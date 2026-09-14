import {act, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
// eslint-disable-next-line no-unused-vars
import Pixel from './Pixel'

const response = value => ({ok:true,json:async()=>value})
const saved = () => JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
beforeEach(()=>{localStorage.clear();vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})

it.each(['connecting','unreachable','switching'])('retains a local draft while %s without sending on Enter',async state=>{
  fetch.mockImplementation(async url=>{
    if (url !== '/api/pixel/status') return response({})
    if (state === 'connecting') return new Promise(()=>{})
    if (state === 'unreachable') throw new TypeError('Network unavailable')
    return response({available:false,state:'model_switching'})
  })
  render(<Pixel/>)
  if (state !== 'connecting') await screen.findByText(state==='switching'?'Switching model...':'Degraded')
  const input = screen.getByRole('textbox')
  expect(input).toBeEnabled()
  fireEvent.change(input,{target:{value:'Prepare this before the model is ready'}})
  fireEvent.keyDown(input,{key:'Enter',code:'Enter'})
  expect(input).toHaveValue('Prepare this before the model is ready')
  expect(saved().draft).toBe('Prepare this before the model is ready')
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/chat/stream')).toHaveLength(0)
})

it('can insert local prompt commands while the backend is unavailable',async()=>{
  fetch.mockResolvedValue(response({available:false}))
  render(<Pixel/>)
  await screen.findByText('Degraded')
  fireEvent.click(screen.getByRole('button',{name:'Open prompt commands'}))
  fireEvent.click(screen.getByRole('button',{name:/Plan Milestones/}))
  expect(screen.getByRole('textbox')).toHaveValue('Plan this outcome with milestones and exact completion criteria: ')
  expect(saved().draft).toMatch(/^Plan this outcome/)
  expect(screen.getByTitle('Send')).toBeDisabled()
})

it('enables explicit Send after readiness returns but never auto-submits the retained draft',async()=>{
  vi.useFakeTimers({toFake:['setTimeout','clearTimeout']})
  let available = false
  fetch.mockImplementation(async()=>response({available}))
  render(<Pixel/>)
  await act(async()=>{})
  const input = screen.getByRole('textbox')
  expect(input).toBeEnabled()
  fireEvent.change(input,{target:{value:'A draft waiting for readiness'}})
  expect(screen.getByTitle('Send')).toBeDisabled()
  available = true
  await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(input).toHaveValue('A draft waiting for readiness')
  expect(fetch.mock.calls.some(([url])=>url==='/api/pixel/chat/stream')).toBe(false)
})

it('still prevents editing when a restored task is active',async()=>{
  localStorage.setItem('ods.pixel.chat.v1',JSON.stringify({schema:1,chatId:'active-chat',requestId:'active-request',inFlight:true,
    draft:'Preserved draft',messages:[{role:'user',content:'Still running'}]}))
  fetch.mockImplementation(async url=>response(url==='/api/pixel/status'?{available:false}:url==='/api/pixel/chat/activity'?{state:'active'}: {state:'pending'}))
  render(<Pixel/>)
  expect(screen.getByRole('textbox')).toBeDisabled()
  await waitFor(()=>expect(screen.getByText('Working in this chat')).toBeInTheDocument())
  expect(screen.getByRole('textbox')).toBeDisabled()
})
