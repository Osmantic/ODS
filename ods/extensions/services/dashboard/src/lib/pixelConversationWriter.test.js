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
