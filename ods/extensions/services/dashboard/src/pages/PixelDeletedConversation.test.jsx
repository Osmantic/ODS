import {act, cleanup, fireEvent, screen, waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import {readConversations, saveConversation, SELECT_EVENT} from '../lib/pixelConversations'

const response = (body, status = 200) => ({ok:status >= 200 && status < 300, status, json:async () => body})
const deleted = () => response({detail:{code:'conversation_deleted',message:'Start a new conversation.'}},410)
const stored = () => JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))
const history = [{role:'user',content:'Earlier acknowledged question'}, {role:'assistant',content:'Earlier acknowledged answer',status:'done'}]
const draft = '  Keep this exact draft: café\nnext line  '
let stream, requests, mockFetch
function completed(text = 'Explicitly sent', error = null) {
  let index = 0
  const frames = [`data: ${JSON.stringify(error ? {error} : {choices:[{delta:{content:text}}]})}\n\n`, 'data: [DONE]\n\n']
  return {ok:true,status:200,body:{getReader:() => ({read:async () => index < frames.length
    ? {done:false,value:new TextEncoder().encode(frames[index++])} : {done:true},releaseLock(){}})}}
}
beforeEach(() => {
  localStorage.clear(); requests = []; stream = async () => deleted()
  mockFetch = vi.fn(async (url, options) => {
    if (url === '/api/pixel/status') return response({available:true})
    if (url === '/api/pixel/chat/context') return response({schemaVersion:1,status:'ready',sessionRevision:'revision',
      context:{used:100,window:32768},model:{id:'test-model',provider:'ods-gateway',contextWindow:32768,imageInput:'supported',routeFingerprint:'a'.repeat(64)},
      compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    if (url === '/api/pixel/chat/stream') { requests.push(JSON.parse(options.body)); return stream(options) }
    if (url === '/api/pixel/chat/result') return response({state:'unknown',events:''})
    if (url === '/api/pixel/chat/activity') return response({state:'unknown'})
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch',mockFetch)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function send() {
  render(<Pixel/>); await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:draft}})
  await waitFor(() => expect(screen.getByTitle('Send')).not.toBeDisabled())
  fireEvent.click(screen.getByTitle('Send'))
}

it.each([{messages:[]},{messages:history}])('preserves a refused draft and acknowledged history through reload until explicit new-chat and Send (%j)',async ({messages}) => {
  saveConversation({schema:1,chatId:'deleted-chat',messages,draft:''})
  await send()
  expect(await screen.findByRole('button',{name:'New chat with draft'})).toBeEnabled()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(stored()).toMatchObject({chatId:'deleted-chat',requestId:null,inFlight:false,interrupted:false,conversationDeleted:true,draft,messages})
  expect(mockFetch.mock.calls.some(([url]) => url === '/api/pixel/chat/result' || url === '/api/pixel/chat/activity')).toBe(false)
  const original = stored()
  cleanup(); render(<Pixel/>); await screen.findByText('Available')
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(requests).toHaveLength(1)
  fireEvent.click(screen.getByRole('button',{name:'New chat with draft'}))
  await waitFor(() => expect(stored().chatId).not.toBe('deleted-chat'))
  const newId = stored().chatId
  expect(stored()).toMatchObject({messages:[],draft,draftImages:[],requestId:null,inFlight:false,interrupted:false})
  expect(readConversations().find(chat => chat.chatId === 'deleted-chat')).toEqual(original)
  expect(requests).toHaveLength(1)
  stream = async () => completed()
  await waitFor(() => expect(screen.getByTitle('Send')).not.toBeDisabled())
  fireEvent.click(screen.getByTitle('Send'))
  expect(await screen.findByText('Explicitly sent')).toBeVisible()
  expect(requests).toHaveLength(2)
  expect(requests[1].chat_id).toBe(newId)
  expect(requests[1].request_id).not.toBe(requests[0].request_id)
  expect(requests[1].messages).toEqual([{role:'user',content:draft.trim()}])
})

it.each(['untyped410','wrongCode','network','acceptedProviderText','providerErrorFrame'])('does not classify %s as authoritative deletion or erase an uncertain attempt',async kind => {
  stream = async () => {
    if (kind === 'network') throw new TypeError('network failure')
    if (kind === 'acceptedProviderText') return completed('Provider says conversation_deleted HTTP 410')
    if (kind === 'providerErrorFrame') return completed('',{code:'conversation_deleted',status:410,message:'Provider says deleted'})
    return response({detail:kind === 'wrongCode' ? {code:'other'} : 'This conversation was deleted.'},410)
  }
  await send()
  await waitFor(() => expect(stored().inFlight).toBe(false))
  expect(stored().conversationDeleted).not.toBe(true)
  expect(screen.queryByRole('button',{name:'New chat with draft'})).toBeNull()
  expect(requests).toHaveLength(1)
  if (kind === 'acceptedProviderText') {
    expect(stored()).toMatchObject({interrupted:false,requestId:null})
    expect(await screen.findByText('Provider says conversation_deleted HTTP 410')).toBeVisible()
  } else {
    expect(stored().interrupted).toBe(true)
    expect(stored().requestId).toBe(requests[0].request_id)
  }
})

it('keeps newer saved edits intact when a refused attempt returns to a stale editor',async () => {
  saveConversation({schema:1,chatId:'deleted-chat',messages:history,draft:''})
  let finish
  stream = () => new Promise(resolve => {finish=resolve})
  await send(); await waitFor(() => expect(requests).toHaveLength(1))
  const newer = saveConversation({...stored(),draft:'Newer draft from the other editor'})
  await act(async () => finish(deleted()))
  expect(await screen.findByText(/This conversation changed in another tab/)).toBeVisible()
  expect(stored()).toEqual(newer)
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(requests).toHaveLength(1)
})

it('never copies chat-bound image receipts into the explicit new chat',async () => {
  const image = {id:`img-${'1'.repeat(32)}`,sha256:'b'.repeat(64),media_type:'image/png',bytes:80,width:1,height:1}
  saveConversation({schema:1,chatId:'deleted-image-chat',messages:history,draft:'',draftImages:[image]})
  await send()
  expect(await screen.findByText(/Attachments will not be copied/)).toBeVisible()
  expect(stored().draftImages).toEqual([image])
  fireEvent.click(screen.getByRole('button',{name:'New chat with draft'}))
  await waitFor(() => expect(stored().chatId).not.toBe('deleted-image-chat'))
  expect(stored()).toMatchObject({draft,draftImages:[],messages:[]})
  expect(readConversations().find(chat => chat.chatId === 'deleted-image-chat').draftImages).toEqual([image])
  expect(requests).toHaveLength(1)
  expect(mockFetch.mock.calls.some(([,options]) => options?.method === 'DELETE')).toBe(false)
})

it('restores the blocked state when selecting the retained refused conversation from the library',async () => {
  saveConversation({schema:1,chatId:'deleted-chat',messages:history,draft,conversationDeleted:true})
  saveConversation({schema:1,chatId:'other-chat',messages:[],draft:'Other draft'})
  render(<Pixel/>); await screen.findByText('Available')
  fireEvent(window,new CustomEvent(SELECT_EVENT,{detail:'deleted-chat'}))
  expect(await screen.findByRole('button',{name:'New chat with draft'})).toBeVisible()
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue(draft)
  expect(screen.getByTitle('Send')).toBeDisabled()
  expect(requests).toHaveLength(0)
})
