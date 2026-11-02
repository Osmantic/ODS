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

it('offers Resume model inside Portal for an unloaded model and keeps the draft',async()=>{
  const detail='The model is unloaded, so Portal is paused. Choose Resume model to load it again.'
  render(<Pixel/>); await act(async()=>{})
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Keep this draft intact'}})
  await poll({available:false,model:null,state:'model_unloaded',detail,recovery:'resume-model'})
  expect(screen.getByText('Model unloaded').querySelector('.animate-spin')).toBeNull()
  expect(screen.queryByText('Switching model...')).toBeNull()
  expect(screen.queryByLabelText('Model activation')).toBeNull()
  expect(screen.getByText(detail)).toBeVisible()
  // The fix is a button here, not a link to another page.
  expect(screen.queryByRole('link',{name:'Open Models'})).toBeNull()
  const resume=within(screen.getByRole('region',{name:'Portal recovery'})).getByRole('button',{name:'Resume model'})
  const composer=screen.getByPlaceholderText('Messages are held until the model is resumed')
  expect(composer).toBeDisabled()
  expect(composer).toHaveValue('Keep this draft intact')
  expect(screen.getByTitle('Send')).toBeDisabled()
  // Nothing is resumed until the owner chooses it.
  expect(fetch.mock.calls.every(([url,options])=>options?.method!=='POST' || url==='/api/pixel/chat/context')).toBe(true)
  fetch.mockImplementationOnce(async()=>json({status:'started'}))
  fireEvent.click(resume)
  await act(async()=>{})
  const resumes=fetch.mock.calls.filter(([url])=>url==='/api/models/runtime/start')
  expect(resumes.map(([,options])=>[options.method,options.body])).toEqual([['POST','{}']])
  expect(screen.getByText('Done. Checking Portal again…')).toBeVisible()
  await poll({available:true})
  expect(screen.queryByRole('region',{name:'Portal recovery'})).toBeNull()
  expect(screen.getByRole('textbox')).toHaveValue('Keep this draft intact')
  expect(screen.getByTitle('Send')).toBeEnabled()
})

it('offers Recover model switch for a switch that stopped and names the next step if it fails',async()=>{
  const detail='A model switch stopped before it finished, so new messages are held. Choose Recover model switch to finish or undo it.'
  render(<Pixel/>); await act(async()=>{})
  await poll({available:false,model:null,state:'model_recovery_required',detail,recovery:'recover-model'})
  expect(screen.getByText('Model switch stopped')).toBeVisible()
  expect(screen.queryByText('Switching model...')).toBeNull()
  expect(screen.getByTitle('Send')).toBeDisabled()
  fetch.mockImplementationOnce(async()=>({ok:false,status:409,json:async()=>({pending:true,phase:'held',
    transactionId:'a'.repeat(64),reason:'model-recovery-unavailable'})}))
  fireEvent.click(within(screen.getByRole('region',{name:'Portal recovery'})).getByRole('button',{name:'Recover model switch'}))
  await act(async()=>{})
  const failure=within(screen.getByRole('region',{name:'Portal recovery'})).getByText(/could not be finished/)
  expect(failure).toHaveTextContent('Open Models to read its current state')
  expect(within(failure).getByRole('link',{name:'Get help on Discord'})).toHaveAttribute('href','https://discord.gg/4ntNp9MAwC')
  expect(fetch.mock.calls.filter(([url,options])=>url==='/api/models/recovery'&&options?.method==='POST')).toHaveLength(1)
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
