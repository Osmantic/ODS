import { CHAT_KEY, readConversations, saveConversation, conversationTitle, deleteConversation } from './pixelConversations'

beforeEach(() => localStorage.clear())
const chat = (id, text) => ({ schema: 1, chatId: id, messages: [{ role: 'user', content: text }], preview: null })

test('migrates the current conversation without deleting the original', () => {
  localStorage.setItem(CHAT_KEY, JSON.stringify(chat('legacy', 'Original task')))
  saveConversation(chat('second', 'Second task'))
  expect(readConversations().map(item => item.chatId)).toEqual(['second', 'legacy'])
})

test('new empty tasks do not erase earlier conversations', () => {
  saveConversation(chat('first', 'Keep me'))
  saveConversation({ schema: 1, chatId: 'new', messages: [] })
  expect(readConversations().map(item => item.chatId)).toEqual(['first'])
})

test('updates a conversation without creating duplicate entries', () => {
  saveConversation(chat('first', 'Keep me'))
  saveConversation({ ...chat('first', 'Keep me'), inFlight: true })
  expect(readConversations()).toHaveLength(1)
  expect(readConversations()[0].inFlight).toBe(true)
  expect(conversationTitle(readConversations()[0])).toBe('Keep me')
})

test('clearing an unsent draft removes its saved text without deleting other chats', () => {
  saveConversation(chat('sent', 'Keep this conversation'))
  saveConversation({ schema: 1, chatId: 'draft', messages: [], draft: 'Discard this text' })
  saveConversation({ schema: 1, chatId: 'draft', messages: [], draft: '' })
  expect(readConversations().map(item => item.chatId)).toEqual(['sent'])
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).draft).toBe('')
  saveConversation({ schema: 1, chatId: 'next', messages: [], draft: '' })
  expect(readConversations().map(item => item.chatId)).toEqual(['sent'])
  // Erasing a draft is not deletion: the same active chat can be edited again.
  saveConversation({ schema: 1, chatId: 'draft', messages: [], draft: 'Replacement' })
  expect(conversationTitle(readConversations().find(item => item.chatId === 'draft'))).toBe('Replacement')
})

test('rejects invalid identities', () => {
  expect(() => saveConversation(chat('../bad', 'test'))).toThrow()
})

test('a damaged current-chat pointer does not hide or overwrite the saved library',()=>{
  saveConversation(chat('saved','Keep this history'))
  localStorage.setItem(CHAT_KEY,'{broken')
  expect(readConversations().map(item=>item.chatId)).toEqual(['saved'])
  saveConversation(chat('new','New task'))
  expect(readConversations().map(item=>item.chatId)).toContain('saved')
})

test.each(['{broken','{}'])('refuses to overwrite an unreadable library (%s)',raw=>{
  localStorage.setItem('ods.pixel.conversations.v1',raw)
  expect(()=>saveConversation(chat('new','New task'))).toThrow()
  expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe(raw)
})

test('deletes only the selected chat and prevents stale tabs from restoring it', () => {
  const removed = {...chat('first','Remove me'), preview:{siteId:'preserved-preview'}}
  saveConversation(removed)
  saveConversation(chat('second','Keep me'))
  deleteConversation('first')
  expect(readConversations().map(item=>item.chatId)).toEqual(['second'])
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).chatId).toBe('second')
  expect(()=>saveConversation(removed)).toThrow(/deleted/)
  localStorage.setItem(CHAT_KEY,JSON.stringify(removed))
  expect(readConversations().map(item=>item.chatId)).toEqual(['second'])
})
test('clears the current chat and refuses active or interrupted tasks',()=>{
  saveConversation({...chat('active','Working'),inFlight:true})
  expect(()=>deleteConversation('active')).toThrow(/Stop/)
  saveConversation({...chat('active','Working'),inFlight:false,interrupted:true})
  expect(()=>deleteConversation('active')).toThrow(/Stop/)
  saveConversation({...chat('active','Done'),inFlight:false,interrupted:false})
  deleteConversation('active')
  expect(localStorage.getItem(CHAT_KEY)).toBeNull()
  expect(readConversations()).toEqual([])
})
test('preserves unknown records, including null, while updating and deleting valid chats', () => {
  const damaged = [null, 42, {schema:1, chatId:'broken', messages:[null]},
    {schema:1, chatId:123, messages:[]}]
  const retained = {...chat('valid','Keep working'), updatedAt:{toString:1,valueOf:2}}
  localStorage.setItem('ods.pixel.conversations.v1', JSON.stringify([...damaged,retained]))
  expect(readConversations().map(item => item.chatId)).toEqual(['valid'])
  saveConversation({...retained,draft:'Still works'})
  expect(readConversations()[0].draft).toBe('Still works')
  deleteConversation('valid')
  expect(JSON.parse(localStorage.getItem('ods.pixel.conversations.v1'))).toEqual(damaged)
})

test('deleting a conversation also removes its retained name, pin and archive metadata', () => {
  saveConversation(chat('private','Private conversation'))
  saveConversation(chat('keep','Other conversation'))
  localStorage.setItem('ods.pixel.chat-labels.v1.private',JSON.stringify({title:'Private project title',pinned:true,archived:true}))
  localStorage.setItem('ods.pixel.chat-labels.v1.keep',JSON.stringify({title:'Keep this title',pinned:false,archived:false}))
  deleteConversation('private')
  expect(localStorage.getItem('ods.pixel.chat-labels.v1.private')).toBeNull()
  expect(conversationTitle(readConversations()[0])).toBe('Keep this title')
  expect(readConversations().map(item => item.chatId)).toEqual(['keep'])
  expect(() => saveConversation(chat('private','Stale tab'))).toThrow(/deleted/)
})

test('reports failed metadata cleanup and permits a safe retry after the conversation tombstone is written', () => {
  saveConversation(chat('private','Private conversation'))
  const key = 'ods.pixel.chat-labels.v1.private'
  localStorage.setItem(key,JSON.stringify({title:'Private title',pinned:false,archived:false}))
  const remove = window.Storage.prototype.removeItem
  const fail = vi.spyOn(window.Storage.prototype,'removeItem').mockImplementation(function (name) {
    if (name === key) throw new globalThis.DOMException('Denied','SecurityError')
    return remove.call(this,name)
  })
  try {
    expect(() => deleteConversation('private')).toThrow()
    expect(localStorage.getItem(key)).not.toBeNull()
    expect(readConversations()).toEqual([])
    expect(() => saveConversation(chat('private','Stale tab'))).toThrow(/deleted/)
  } finally {fail.mockRestore()}
  deleteConversation('private')
  expect(localStorage.getItem(key)).toBeNull()
})
