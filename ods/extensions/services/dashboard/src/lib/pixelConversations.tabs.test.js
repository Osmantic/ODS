import {CHAT_KEY, TAB_CHAT_KEY, createConversationWriter, deleteConversation, isConversationDeleted,
  readActiveConversation, readConversations, rememberActiveConversation, saveConversation} from './pixelConversations'

const PREFIX='ods.pixel.conversation.v2.'
const chat=(id,draft='original')=>({schema:1,chatId:id,messages:[],draft})
const tab=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),clear:()=>values.clear()}}
beforeEach(()=>localStorage.clear())
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

it.each([CHAT_KEY,PREFIX+'first','ods.pixel.conversations.v1'])('isolates disjoint commits interleaved at %s',boundary=>{
  const first=chat('first'),second=chat('second')
  saveConversation(second);saveConversation(first)
  const a=createConversationWriter(readConversations().find(row=>row.chatId==='first'))
  const b=createConversationWriter(readConversations().find(row=>row.chatId==='second'))
  const original=Storage.prototype.setItem
  let armed=true
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if(armed && key===boundary){armed=false;b({...second,draft:'second finished'})}
    return original.call(this,key,value)
  })
  a({...first,draft:'first finished'})
  expect(readConversations().find(row=>row.chatId==='first').draft).toBe('first finished')
  expect(readConversations().find(row=>row.chatId==='second').draft).toBe('second finished')
  expect(()=>b({...second,draft:'second next turn'})).not.toThrow()
})

it('restores each tab identity despite another tab moving the legacy active pointer',()=>{
  const first=tab(),second=tab()
  vi.stubGlobal('sessionStorage',first);saveConversation(chat('first'))
  vi.stubGlobal('sessionStorage',second);saveConversation(chat('second'))
  expect(JSON.parse(localStorage.getItem(CHAT_KEY)).chatId).toBe('second')
  vi.stubGlobal('sessionStorage',first)
  expect(readActiveConversation().chatId).toBe('first')
  vi.stubGlobal('sessionStorage',second)
  expect(readActiveConversation().chatId).toBe('second')
})

it('a new empty tab selection does not reload another tab conversation',()=>{
  saveConversation(chat('other','Keep its draft'))
  rememberActiveConversation('new-empty')
  expect(sessionStorage.getItem(TAB_CHAT_KEY)).toBe('new-empty')
  expect(readActiveConversation()).toMatchObject({chatId:'new-empty',messages:[],draft:''})
})

it('retains a committed reload source when the per-chat write runs out of quota',()=>{
  const source=chat('partial')
  saveConversation(source)
  const write=createConversationWriter(readActiveConversation())
  const original=Storage.prototype.setItem
  const failure=vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if(key===PREFIX+'partial')throw new DOMException('Full','QuotaExceededError')
    return original.call(this,key,value)
  })
  expect(()=>write({...source,draft:'Committed before quota'})).toThrow('Full')
  expect(readActiveConversation().draft).toBe('Committed before quota')
  failure.mockRestore()
  expect(()=>write({...source,draft:'Retry against own commit'})).not.toThrow()
  expect(readActiveConversation().draft).toBe('Retry against own commit')
})

it.each(['Partial newer draft',''])('retains partial A1 after quota and a different tab moves the pointer (%j)',draft=>{
  const source=chat('partial-a','A0')
  saveConversation(source)
  const ownSession=sessionStorage,otherSession=tab()
  const write=createConversationWriter(readActiveConversation())
  const original=Storage.prototype.setItem
  const failure=vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if(key===PREFIX+'partial-a')throw new DOMException('Full','QuotaExceededError')
    return original.call(this,key,value)
  })
  expect(()=>write({...source,draft})).toThrow('Full')
  failure.mockRestore()
  vi.stubGlobal('sessionStorage',otherSession);saveConversation(chat('other-tab','B1'))
  vi.stubGlobal('sessionStorage',ownSession)
  expect(readActiveConversation().draft).toBe(draft)
  expect(()=>write({...source,draft:'A2 next turn'})).not.toThrow()
  expect(readConversations().find(row=>row.chatId==='partial-a').draft).toBe('A2 next turn')
})

it('refuses to move a foreign partial pointer while its durable write still fails',()=>{
  const source=chat('partial-a','A0')
  saveConversation(source)
  const write=createConversationWriter(readActiveConversation())
  const original=Storage.prototype.setItem
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(key,value){
    if(key===PREFIX+'partial-a')throw new DOMException('Full','QuotaExceededError')
    return original.call(this,key,value)
  })
  expect(()=>write({...source,draft:'A1 protected'})).toThrow('Full')
  expect(()=>saveConversation(chat('other-tab','B1'))).toThrow('Full')
  expect(JSON.parse(localStorage.getItem(CHAT_KEY))).toMatchObject({chatId:'partial-a',draft:'A1 protected'})
  expect(localStorage.getItem(PREFIX+'other-tab')).toBeNull()
})

it('recognizes a library-only exact child after a foreign legacy pointer move',()=>{
  saveConversation(chat('library-child','A0'))
  const old=JSON.parse(localStorage.getItem(CHAT_KEY))
  const write=createConversationWriter(readActiveConversation())
  write({...old,draft:'A1 library child'})
  const next=JSON.parse(localStorage.getItem(CHAT_KEY))
  localStorage.setItem(PREFIX+'library-child',JSON.stringify(old))
  localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify([next]))
  localStorage.setItem(CHAT_KEY,JSON.stringify(chat('legacy-other','B1')))
  expect(readActiveConversation().draft).toBe('A1 library child')
  expect(()=>write({...old,draft:'A2'})).not.toThrow()
})

it('keeps a legacy library-first record authoritative during the first pointer handoff',()=>{
  const latest=chat('legacy','Latest library draft')
  localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify([latest]))
  localStorage.setItem(CHAT_KEY,JSON.stringify(chat('legacy','Older pointer draft')))
  saveConversation(chat('new-task','New draft'))
  expect(readConversations().find(row=>row.chatId==='legacy').draft).toBe('Latest library draft')
})

it('does not ignore an incompatible legacy writer on the same chat',()=>{
  saveConversation(chat('same','Modern saved draft'))
  const write=createConversationWriter(readActiveConversation())
  const legacy={...JSON.parse(localStorage.getItem(CHAT_KEY)),draft:'Legacy unsaved text',persistenceRevision:'external'}
  localStorage.setItem(CHAT_KEY,JSON.stringify(legacy))
  const record=localStorage.getItem(PREFIX+'same')
  expect(()=>write(chat('same','Overwrite attempt'))).toThrow(/changed in another tab/)
  expect(JSON.parse(localStorage.getItem(CHAT_KEY))).toEqual(legacy)
  expect(localStorage.getItem(PREFIX+'same')).toBe(record)
})

it('per-chat tombstones still refuse resurrection after a stale legacy list overwrite',()=>{
  const record=chat('deleted')
  saveConversation(record)
  const write=createConversationWriter(readActiveConversation())
  deleteConversation('deleted')
  localStorage.setItem('ods.pixel.deleted-conversations.v1','[]')
  localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify([record]))
  expect(isConversationDeleted('deleted')).toBe(true)
  expect(readConversations()).toEqual([])
  expect(()=>write({...record,draft:'resurrect'})).toThrow(/deleted/)
})

it('preserves unreadable durable records instead of overwriting history',()=>{
  saveConversation(chat('healthy'))
  localStorage.setItem(PREFIX+'damaged','{broken')
  expect(()=>saveConversation(chat('another'))).toThrow()
  expect(localStorage.getItem(PREFIX+'damaged')).toBe('{broken')
})

it('does not clear an unreadable per-chat deletion marker',()=>{
  saveConversation(chat('guarded'))
  localStorage.setItem('ods.pixel.deleted-conversation.v2.guarded','invalid')
  expect(()=>saveConversation(chat('guarded','Do not revive'))).toThrow(/deletion marker/)
  expect(localStorage.getItem('ods.pixel.deleted-conversation.v2.guarded')).toBe('invalid')
})
