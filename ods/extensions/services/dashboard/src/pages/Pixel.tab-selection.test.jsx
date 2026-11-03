import {cleanup, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
// eslint-disable-next-line no-unused-vars
import Pixel from './Pixel'
import {CHAT_KEY, createConversationWriter, readConversations, rememberActiveConversation, saveConversation} from '../lib/pixelConversations'

const a={schema:1,chatId:'tab-a',messages:[{role:'user',content:'Only the first tab question'},{role:'assistant',content:'First tab answer',status:'done'}],draft:''}
const b={schema:1,chatId:'tab-b',messages:[{role:'user',content:'Only the second tab question'}],draft:''}
const tab=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),clear:()=>values.clear()}}
let requests
beforeEach(()=>{
  localStorage.clear();requests=[]
  saveConversation(a);saveConversation(b);rememberActiveConversation(a.chatId)
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
    if(url==='/api/pixel/status')return {ok:true,json:async()=>({available:true,model:'pixel/default'})}
    if(url==='/api/pixel/chat/stream'){
      requests.push(JSON.parse(options.body))
      let read=false
      return {ok:true,status:200,body:{getReader:()=>({read:async()=>read?{done:true}:(read=true,
        {done:false,value:new TextEncoder().encode('data: {"choices":[{"delta":{"content":"First tab next result"}}]}\n\ndata: [DONE]\n\n')}),releaseLock(){}})}}
    }
    return {ok:false,status:404,json:async()=>({})}
  }))
})
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('reloads its own conversation after another tab moved the shared legacy pointer',async()=>{
  render(<Pixel/>);await screen.findByText('Available')
  expect(screen.getByText(a.messages[0].content)).toBeVisible()
  expect(screen.queryByText(b.messages[0].content)).not.toBeInTheDocument()
  cleanup()
  localStorage.setItem(CHAT_KEY,JSON.stringify(readConversations().find(row=>row.chatId===b.chatId)))
  render(<Pixel/>);await screen.findByText('Available')
  expect(screen.getByText(a.messages[0].content)).toBeVisible()
  expect(screen.queryByText(b.messages[0].content)).not.toBeInTheDocument()
  expect(requests).toEqual([])
})

it('saves a response and starts the next turn after an interleaved foreign-chat save',async()=>{
  render(<Pixel/>);await screen.findByText('Available')
  const other=createConversationWriter(readConversations().find(row=>row.chatId===b.chatId))
  const original=Storage.prototype.setItem,ownTab=sessionStorage,otherTab=tab()
  let armed=true
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if(armed && key==='ods.pixel.conversations.v1'){
      armed=false;vi.stubGlobal('sessionStorage',otherTab)
      try{other({...b,draft:'Second tab updated while first typed'})}finally{vi.stubGlobal('sessionStorage',ownTab)}
    }
    return original.call(this,key,value)
  })
  fireEvent.change(screen.getByPlaceholderText(/Message Portal/),{target:{value:'Continue in the first tab'}})
  await waitFor(()=>expect(screen.getByTitle('Send')).not.toBeDisabled())
  fireEvent.click(screen.getByTitle('Send'))
  expect(await screen.findByText('First tab next result')).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(requests[0].chat_id).toBe(a.chatId)
  expect(readConversations().find(row=>row.chatId===b.chatId).draft).toBe('Second tab updated while first typed')
  fireEvent.change(screen.getByPlaceholderText(/Message Portal/),{target:{value:'One more first-tab turn'}})
  await waitFor(()=>expect(screen.getByTitle('Send')).not.toBeDisabled())
  fireEvent.click(screen.getByTitle('Send'))
  await waitFor(()=>expect(requests).toHaveLength(2))
  expect(requests[1].chat_id).toBe(a.chatId)
  expect(screen.queryByText(/This conversation changed in another tab/)).not.toBeInTheDocument()
})
