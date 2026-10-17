import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import {CHAT_KEY, saveConversation} from '../lib/pixelConversations'
import Pixel from './Pixel'

const json = data => ({ok:true,json:async()=>data})
let status
beforeEach(()=>{
  localStorage.clear()
  vi.useFakeTimers()
  status = {available:true}
  vi.stubGlobal('fetch',vi.fn(async url=>{
    if(url==='/api/pixel/status')return json(status)
    if(url==='/api/pixel/chat/context')return json({schemaVersion:1,status:'missing',sessionRevision:null,
      context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    return {ok:false,status:404,json:async()=>({})}
  }))
})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
const poll = async value=>{status=value;await act(async()=>{await vi.advanceTimersByTimeAsync(3000)})}
const switching = phase=>({available:false,state:'model_switching',modelActivation:{active:true,phase,
  failureCode:phase.startsWith('roll')?'runtime_load_failed':null}})

it.each([false,true])('shows owned rollback with existing history=%s and preserves the draft through verified recovery',async history=>{
  if(history)saveConversation({schema:1,chatId:'switch-history',messages:[{role:'user',content:'Existing conversation'}],draft:''})
  render(<Pixel/>); await act(async()=>{})
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Keep this draft intact'}})
  await poll(switching('loading'))
  expect(screen.getByLabelText('Model activation')).toHaveTextContent('Loading selected model')
  expect(screen.getByTitle('Send')).toBeDisabled()
  await poll(switching('rolling_back'))
  const notice=screen.getByRole('alert',{name:'Model activation'})
  expect(notice).toHaveTextContent('Model activation failed; restoring previous model')
  expect(notice).toHaveTextContent('could not be loaded')
  expect(notice).toHaveTextContent('Try a different GGUF or model, or update ODS')
  expect(notice).not.toHaveTextContent(/corrupt|available again/)
  expect(notice).not.toHaveTextContent('previous model restored')
  expect(within(notice).getByRole('link',{name:'View model status and recovery'})).toHaveAttribute('href','/models')
  expect(screen.getByRole('textbox')).toHaveValue('Keep this draft intact')
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).draft).toBe('Keep this draft intact')
  await poll(switching('rollback_verifying'))
  expect(screen.getByLabelText('Model activation')).toHaveTextContent('Checking the previous model')
  expect(screen.getByTitle('Send')).toBeDisabled()
  await poll({available:true,modelActivation:{active:false,outcome:'rolled_back',failureCode:'runtime_load_failed'}})
  expect(screen.getByRole('alert',{name:'Model activation'})).toHaveTextContent('previous model restored')
  expect(screen.getByRole('alert',{name:'Model activation'})).toHaveTextContent('Portal is available again')
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(screen.getByRole('textbox')).toHaveValue('Keep this draft intact')
  expect(fetch.mock.calls.every(([url,options])=>options?.method!=='POST' || url==='/api/pixel/chat/context')).toBe(true)
})

it('does not turn a long or legacy pending switch into a failure or automatic recovery',async()=>{
  status={available:false,state:'model_switching'}
  render(<Pixel/>); await act(async()=>{})
  await act(async()=>{await vi.advanceTimersByTimeAsync(120000)})
  const notice=screen.getByRole('status',{name:'Model activation'})
  expect(notice).toHaveTextContent('Model change has not finished')
  expect(notice).not.toHaveTextContent(/failed|restored|automatically/)
  expect(notice.querySelector('.animate-spin')).toBeNull()
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(fetch.mock.calls.every(([url,options])=>options?.method!=='POST' || url==='/api/pixel/chat/context')).toBe(true)
})

it('requires an explicit unconfirmed outcome and never enables chat from diagnostic metadata',async()=>{
  status={available:false,state:'model_switching',modelActivation:{active:false,outcome:'rollback_unconfirmed',failureCode:'rollback_unconfirmed'}}
  render(<Pixel/>); await act(async()=>{})
  expect(screen.getByRole('alert',{name:'Model activation'})).toHaveTextContent('Model recovery needs attention')
  expect(screen.getByTitle('Send')).toBeDisabled()
  await poll({available:false,state:'model_switching',modelActivation:{active:false,outcome:'activated',failureCode:null}})
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(screen.getByLabelText('Model activation')).toHaveTextContent('has not finished')
})

it('ignores malformed diagnostic metadata and removes a stale diagnosis on the next status read',async()=>{
  status=switching('rolling_back')
  render(<Pixel/>);await act(async()=>{})
  await poll({available:false,state:'model_switching',modelActivation:{active:true,phase:'private-path/error',failureCode:'raw traceback'}})
  const notice=screen.getByLabelText('Model activation')
  expect(notice).toHaveTextContent('Model change has not finished')
  expect(notice).not.toHaveTextContent(/raw traceback|private-path|activation failed/)
  await poll({available:true})
  expect(screen.queryByLabelText('Model activation')).toBeNull()
})

it('keeps unverified health blocked, polls again, and preserves the draft without replay',async()=>{
  render(<Pixel/>);await act(async()=>{})
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Keep my unsent request'}})
  const unknown={available:false,state:'model_unverified',
    detail:"Portal could not verify the local model's health. It will check again shortly; your draft is preserved."}
  await poll(unknown)
  expect(screen.getByText(unknown.detail)).toBeInTheDocument()
  expect(screen.getByTitle('Send')).toBeDisabled()
  const previousReads=fetch.mock.calls.filter(([url])=>url==='/api/pixel/status').length
  await poll(unknown)
  expect(fetch.mock.calls.filter(([url])=>url==='/api/pixel/status')).toHaveLength(previousReads+1)
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(screen.getByRole('textbox')).toHaveValue('Keep my unsent request')
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).draft).toBe('Keep my unsent request')
  await poll({available:true})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(screen.getByRole('textbox')).toHaveValue('Keep my unsent request')
  expect(fetch.mock.calls.every(([url,options])=>options?.method!=='POST' || url==='/api/pixel/chat/context')).toBe(true)
})
