import {CHAT_KEY, createConversationWriter, deleteConversation, readConversations, saveConversation} from './pixelConversations'

const chat = {schema:1,chatId:'writer-chat',messages:[],draft:'Initial draft'}
const current = () => JSON.parse(localStorage.getItem(CHAT_KEY))
beforeEach(()=>localStorage.clear())
afterEach(()=>vi.restoreAllMocks())

it('detects changed contents even when timestamps are equal',()=>{
  vi.spyOn(Date,'now').mockReturnValue(42)
  saveConversation(chat)
  const write = createConversationWriter(current())
  saveConversation({...chat,draft:'Newer draft'})
  expect(()=>write({...chat,draft:'Stale draft'})).toThrow(/changed in another tab/)
  expect(current().draft).toBe('Newer draft')
})

it('does not reject an identical save with a different timestamp',()=>{
  saveConversation(chat)
  const write = createConversationWriter(current())
  const second = {...current(),updatedAt:0}
  localStorage.setItem(CHAT_KEY,JSON.stringify(second))
  write({...chat,draft:'Intentional edit'})
  expect(current().draft).toBe('Intentional edit')
})

it('advances its checkpoint after active storage commits but the library write fails',()=>{
  saveConversation(chat)
  const write = createConversationWriter(current())
  const setItem = Storage.prototype.setItem
  const failure = vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if (key === 'ods.pixel.conversations.v1') throw new globalThis.DOMException('Full','QuotaExceededError')
    return setItem.call(this,key,value)
  })
  expect(()=>write({...chat,draft:'Partially committed edit'})).toThrow('Full')
  failure.mockRestore()
  write({...chat,draft:'Successful retry'})
  expect(readConversations()[0].draft).toBe('Successful retry')
})

it('does not advance when the active storage write itself fails',()=>{
  saveConversation(chat)
  const write = createConversationWriter(current())
  const failure = vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('Full')})
  expect(()=>write({...chat,draft:'Unsaved edit'})).toThrow('Full')
  failure.mockRestore()
  write({...chat,draft:'Retry against original'})
  expect(current().draft).toBe('Retry against original')
})

it('can clear and then refill an empty draft absent from the library',()=>{
  const write = createConversationWriter()
  write({...chat,draft:''})
  expect(readConversations()).toEqual([])
  write(chat)
  write({...chat,draft:''})
  write({...chat,draft:'Refilled'})
  expect(current().draft).toBe('Refilled')
})

it('preserves deletion tombstones and requires fresh identities for new work',()=>{
  saveConversation(chat)
  const write = createConversationWriter(current())
  deleteConversation(chat.chatId)
  expect(()=>write(chat)).toThrow(/deleted in another tab/)
  write({...chat,chatId:'new-chat'})
  expect(current().chatId).toBe('new-chat')
  expect(readConversations().map(value=>value.chatId)).toEqual(['new-chat'])
})

const pending = {...chat, requestId:'request-1', inFlight:true, interrupted:false,
  messages:[{role:'user',content:'Build a site'},{role:'assistant',content:'Building'}]}
const terminal = source => ({...source, requestId:null, inFlight:false, interrupted:false,
  messages:[source.messages[0],{role:'assistant',content:'Finished',status:'done'}]})

function recoverBeforeSenderFinishes() {
  const sender = createConversationWriter()
  sender(pending)
  const source = current()
  const observer = createConversationWriter(source)
  observer.recover(terminal(source))
  return {sender, observer, source}
}

it('hands an exact automatic recovery back to its originating writer after late partials',()=>{
  const {sender,source} = recoverBeforeSenderFinishes()
  const recovered = localStorage.getItem(CHAT_KEY)
  sender({...source,messages:[source.messages[0],{role:'assistant',content:'Still draining'}]})
  expect(localStorage.getItem(CHAT_KEY)).toBe(recovered)
  sender(terminal(source))
  expect(current().messages.at(-1).content).toBe('Finished')
  expect(current().recoverySource).toBeUndefined()
})

it('does not grant a passive observer the originating writer handoff',()=>{
  const sender = createConversationWriter()
  sender(pending)
  const source = current()
  const observer = createConversationWriter(source)
  createConversationWriter(source).recover(terminal(source))
  const before = localStorage.getItem(CHAT_KEY)
  expect(()=>observer({...terminal(source),draft:'Stale observer edit'})).toThrow(/changed in another tab/)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
})

it.each([
  {draft:'New user draft'}, {contextStart:1}, {compactionRequestId:'other-compaction'},
  {workspaceOpen:true}, {preview:{siteId:'different-site'}},
  {draftImages:[{id:'img-'+'a'.repeat(32),sha256:'b'.repeat(64),media_type:'image/png',bytes:10,width:1,height:1}]},
])('rejects sender overwrite after an observer edits recovered metadata (%j)',edit=>{
  const {sender,observer,source} = recoverBeforeSenderFinishes()
  observer({...current(),...edit})
  const before = localStorage.getItem(CHAT_KEY)
  expect(()=>sender(terminal(source))).toThrow(/changed in another tab/)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
})

it('rejects an edit by an older client that retained the recovery receipt',()=>{
  const {sender,source} = recoverBeforeSenderFinishes()
  const changed = {...current(),draft:'Legacy client edit'}
  localStorage.setItem(CHAT_KEY,JSON.stringify(changed))
  expect(()=>sender(terminal(source))).toThrow(/changed in another tab/)
  expect(current().draft).toBe('Legacy client edit')
})

it('keeps recovery receipts bounded and recovers legacy records without a handoff',()=>{
  const {source} = recoverBeforeSenderFinishes()
  expect(JSON.stringify(current().recoverySource).length).toBeLessThan(300)
  const legacy = {...pending,chatId:'legacy-request'}
  localStorage.setItem(CHAT_KEY,JSON.stringify(legacy))
  createConversationWriter(legacy).recover(terminal(legacy))
  expect(current().messages.at(-1).content).toBe('Finished')
  expect(current().recoverySource).toBeUndefined()
  expect(readConversations().find(item=>item.chatId===source.chatId).messages.at(-1).content).toBe('Finished')
})

it('does not let recovery include an edit to the saved draft or request history',()=>{
  const sender = createConversationWriter()
  sender(pending)
  const source = current(), observer = createConversationWriter(source)
  expect(()=>observer.recover({...terminal(source),draft:'Edited during recovery'})).toThrow(/Invalid conversation recovery/)
  expect(()=>observer.recover({...terminal(source),messages:[{role:'user',content:'Different request'},terminal(source).messages[1]]})).toThrow(/Invalid conversation recovery/)
  expect(current()).toEqual(source)
})

it('can activate a recovered conversation without granting ownership or removing its handoff',()=>{
  const {sender,source} = recoverBeforeSenderFinishes()
  const recovered = current(), observer = createConversationWriter(recovered)
  saveConversation({...chat,chatId:'other-chat'})
  observer.activate()
  expect(current()).toEqual(recovered)
  sender(terminal(source))
  expect(current().messages.at(-1).content).toBe('Finished')
})

it.each([
  {draft:'Edited in sender'}, {contextStart:1}, {compactionRequestId:'new-context-operation'},
  {workspaceOpen:true}, {preview:{siteId:'another-preview'}},
  {draftImages:[{id:'img-'+'a'.repeat(32),sha256:'b'.repeat(64),media_type:'image/png',bytes:10,width:1,height:1}]},
])('rejects a pending sender edit instead of silently skipping it (%j)',edit=>{
  const {sender,source} = recoverBeforeSenderFinishes()
  const before = localStorage.getItem(CHAT_KEY)
  expect(()=>sender({...source,...edit})).toThrow(/changed in another tab/)
  expect(()=>sender(terminal(source))).toThrow(/changed in another tab/)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
})

it('does not let a delayed Stop acknowledgement replace a fuller recovered cancelled result',()=>{
  const sender = createConversationWriter()
  sender(pending)
  const source = current()
  createConversationWriter(source).recover({...terminal(source),
    messages:[source.messages[0],{role:'assistant',content:'All completed steps. Stopped by you.',status:'stopped'}]})
  const before = localStorage.getItem(CHAT_KEY)
  expect(()=>sender({...terminal(source),
    messages:[source.messages[0],{role:'assistant',content:'First step. Stopped by you.',status:'stopped'}]})).toThrow(/changed in another tab/)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
})

it('keeps unknown saved metadata when checking an otherwise unchanged late partial',()=>{
  const sender = createConversationWriter()
  sender({...pending,futureMetadata:{retain:true}})
  createConversationWriter(current()).recover(terminal(current()))
  const before = localStorage.getItem(CHAT_KEY)
  sender({...pending,messages:[pending.messages[0],{role:'assistant',content:'Draining'}]})
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  sender(terminal(pending))
  expect(current().futureMetadata).toEqual({retain:true})
})
