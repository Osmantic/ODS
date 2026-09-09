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
