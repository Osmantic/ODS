import { CHAT_KEY, createConversationWriter, deleteConversation, readConversations, saveConversation } from './pixelConversations'

const record = (chatId, draft = '', messages = []) => ({schema:1, chatId, messages, draft})
const current = () => JSON.parse(localStorage.getItem(CHAT_KEY))
const other = () => record('other', '', [{role:'user',content:'Keep the owner task'}])
beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

test('an existing writer can already create a different absent chat without rebinding', () => {
  saveConversation(other())
  const writer = createConversationWriter(current())
  expect(() => writer(record('new'))).not.toThrow()
  expect(current().chatId).toBe('new')
  expect(readConversations().find(chat => chat.chatId === 'other').messages).toEqual(other().messages)
})

test('an unrelated tab moving the pointer does not invalidate a committed empty new task', () => {
  saveConversation(other())
  const writer = createConversationWriter(current())
  writer(record('new'))
  expect(readConversations().some(chat => chat.chatId === 'new')).toBe(false)
  saveConversation({...other(), messages:[...other().messages,{role:'assistant',content:'Another tab finished'}]})
  expect(current().chatId).toBe('other')
  expect(() => writer(record('new', 'Make a forest page'))).not.toThrow()
  expect(current()).toMatchObject({chatId:'new',draft:'Make a forest page'})
  expect(readConversations().find(chat => chat.chatId === 'other').messages.at(-1).content).toBe('Another tab finished')
})

test('an unrelated pointer move preserves a committed nonempty draft and its next edit', () => {
  const writer = createConversationWriter()
  writer(record('new', 'First draft'))
  saveConversation(other())
  expect(() => writer(record('new', 'Second draft'))).not.toThrow()
  expect(readConversations().find(chat => chat.chatId === 'new').draft).toBe('Second draft')
  expect(readConversations().find(chat => chat.chatId === 'other').messages).toEqual(other().messages)
})

test('an emptied draft remains editable after another tab moves the active pointer', () => {
  const writer = createConversationWriter()
  writer(record('new', 'Discarded draft'))
  writer(record('new'))
  saveConversation(other())
  expect(() => writer(record('new', 'Replacement draft'))).not.toThrow()
  expect(current().draft).toBe('Replacement draft')
  expect(readConversations().find(chat => chat.chatId === 'other').messages).toEqual(other().messages)
})

test('a real latest revision of the empty task is still protected from a stale writer', () => {
  const writer = createConversationWriter()
  writer(record('new'))
  saveConversation(record('new', 'Another tab owns this draft'))
  saveConversation(other())
  const before = localStorage.getItem(CHAT_KEY)
  expect(() => writer(record('new', 'Stale local edit'))).toThrow(/changed in another tab/)
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
  expect(readConversations().find(chat => chat.chatId === 'new').draft).toBe('Another tab owns this draft')
})

test('a genuinely deleted task cannot be resurrected by an omitted empty baseline', () => {
  const writer = createConversationWriter()
  writer(record('new'))
  saveConversation(record('new', 'Other tab made it visible in history'))
  deleteConversation('new')
  saveConversation(other())
  expect(() => writer(record('new', 'Stale edit after deletion'))).toThrow(/deleted in another tab/)
  expect(readConversations().some(chat => chat.chatId === 'new')).toBe(false)
  expect(current().chatId).toBe('other')
})

test('a colliding identity cannot replace an existing conversation', () => {
  saveConversation(other())
  const writer = createConversationWriter(current())
  saveConversation(record('collision', 'Protected draft'))
  expect(() => writer(record('collision', 'Stale replacement'))).toThrow(/changed in another tab/)
  expect(current().draft).toBe('Protected draft')
})

test.each([
  {requestId:'request-1'}, {inFlight:true}, {interrupted:true}, {compactionRequestId:'compact-1'},
  {contextStart:1}, {preview:{siteId:'retained'}}, {futureRecoveryMetadata:'preserve'},
])('an absent empty baseline with operation or unknown metadata remains strict (%j)', metadata => {
  const writer = createConversationWriter()
  writer({...record('new'), ...metadata})
  saveConversation(other())
  expect(() => writer(record('new', 'Do not skip pending recovery'))).toThrow(/changed in another tab/)
  expect(current().chatId).toBe('other')
})

test('an omitted empty baseline never overwrites unreadable history', () => {
  const writer = createConversationWriter()
  writer(record('new'))
  saveConversation(other())
  localStorage.setItem('ods.pixel.conversations.v1', '{broken')
  const before = localStorage.getItem(CHAT_KEY)
  expect(() => writer(record('new', 'Preserve raw history'))).toThrow()
  expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe('{broken')
  expect(localStorage.getItem(CHAT_KEY)).toBe(before)
})
