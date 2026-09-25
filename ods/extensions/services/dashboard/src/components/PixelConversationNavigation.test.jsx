import {render,screen,fireEvent,within} from '@testing-library/react'
import PixelConversationNavigation from './PixelConversationNavigation'
import {saveConversation,readConversations,DELETE_EVENT,SELECT_EVENT,deleteConversation} from '../lib/pixelConversations'
import {saveConversationLabels,conversationLabels} from '../lib/pixelConversationLabels'

beforeEach(()=>{
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close = function(){this.removeAttribute('open')}
  saveConversation({schema:1,chatId:'delete-test',messages:[{role:'user',content:'Disposable test'}]})
})
afterEach(()=>{delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; vi.restoreAllMocks()})
test('delete control does not select the chat; cancellation preserves it',()=>{
  const select = vi.fn(); window.addEventListener(SELECT_EVENT,select)
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.click(screen.getByRole('button',{name:'Delete chat: Disposable test'}))
  const dialog=screen.getByRole('dialog',{name:'Delete this chat?'})
  expect(within(dialog).getByText(/Workspace files and published previews are kept/)).toBeVisible()
  expect(select).not.toHaveBeenCalled()
  fireEvent.click(within(dialog).getByRole('button',{name:'Cancel'}))
  expect(readConversations()).toHaveLength(1)
  window.removeEventListener(SELECT_EVENT,select)
})
test('deletes after confirmation and shows refusal errors without hiding the chat',()=>{
  let blocked=true
  const handle=event=>{if(blocked) event.detail.complete('Stop the current task first.'); else {deleteConversation(event.detail.chatId);event.detail.complete('')}}
  window.addEventListener(DELETE_EVENT,handle)
  render(<PixelConversationNavigation collapsed={false}/>)
  fireEvent.click(screen.getByRole('button',{name:'Delete chat: Disposable test'}))
  fireEvent.click(screen.getByRole('button',{name:'Delete chat',exact:true}))
  expect(screen.getByRole('alert')).toHaveTextContent('Stop the current task first.')
  expect(readConversations()).toHaveLength(1)
  blocked=false
  fireEvent.click(screen.getByRole('button',{name:'Delete chat',exact:true}))
  expect(readConversations()).toEqual([])
  expect(screen.queryByRole('button',{name:'Disposable test',exact:true})).not.toBeInTheDocument()
  window.removeEventListener(DELETE_EVENT,handle)
})

test.each([null, {role:'user', content:42}])('keeps the sidebar usable beside malformed retained messages: %j', message => {
  const good = readConversations()[0]
  const broken = {schema:1, chatId:'broken', messages:[message], updatedAt:1}
  const original = JSON.stringify([broken,good])
  localStorage.setItem('ods.pixel.conversations.v1',original)
  render(<PixelConversationNavigation collapsed={false}/>)
  expect(screen.getByRole('button',{name:'Disposable test',exact:true})).toBeVisible()
  expect(localStorage.getItem('ods.pixel.conversations.v1')).toBe(original)
  saveConversation({...good,draft:'New draft'})
  expect(readConversations()[0].draft).toBe('New draft')
  expect(JSON.parse(localStorage.getItem('ods.pixel.conversations.v1'))).toContainEqual(broken)
  deleteConversation(good.chatId)
  expect(readConversations()).toEqual([])
  expect(JSON.parse(localStorage.getItem('ods.pixel.conversations.v1'))).toEqual([broken])
})

const publication=relativeDirectory=>({schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory,
  siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),entrySha256:'b'.repeat(64),port:9437,
  url:`http://site-${'a'.repeat(24)}.localhost:9437/site-${'a'.repeat(24)}/`,files:2,bytes:500})
const projectChat=(index)=>({schema:1,chatId:`project-${index}`,messages:[{role:'user',content:`Build project ${index}`},
  {role:'assistant',content:'Done',publication:publication(`Playground/site-${index}`)}]})

test('shows five actual project folders, reveals more, and never mixes them into Recent',()=>{
  for(let index=0;index<7;index++)saveConversation(projectChat(index))
  render(<PixelConversationNavigation collapsed={false}/>)
  const root=screen.getByText('Playground').closest('details')
  expect([...root.querySelectorAll('summary[title]')]).toHaveLength(5)
  const more=screen.getByRole('button',{name:'Show more Playground projects'})
  expect(more).toHaveTextContent('Mostrar mais (2)')
  fireEvent.click(more)
  expect([...root.querySelectorAll('summary[title]')]).toHaveLength(7)
  expect(screen.getByRole('button',{name:'Show fewer Playground projects'})).toHaveTextContent('Mostrar menos')
  const recent=screen.getByText('Recent').closest('details')
  expect(within(recent).getByRole('button',{name:'Disposable test',exact:true})).toBeVisible()
  expect(within(recent).queryByText('Build project 6')).toBeNull()
  expect(screen.getAllByRole('button',{name:'Build project 6',exact:true})).toHaveLength(1)
  const select=vi.fn();window.addEventListener(SELECT_EVENT,select)
  fireEvent.click(screen.getByRole('button',{name:'Build project 6',exact:true}))
  expect(select.mock.calls[0][0].detail).toBe('project-6')
  window.removeEventListener(SELECT_EVENT,select)
  const archive=screen.getByRole('button',{name:'Archived (0)'})
  expect(archive.compareDocumentPosition(recent)&Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
})

test('keeps pinned and archived projects out of Recent and preserves legacy folder names',()=>{
  saveConversation(projectChat(0))
  saveConversationLabels('project-0',{...conversationLabels('project-0'),pinned:true},conversationLabels('project-0'))
  saveConversation({...projectChat(1),preview:undefined,messages:[{role:'user',content:'Legacy project'},
    {role:'assistant',content:'Done',publication:publication('legacy-site')}]})
  saveConversation(projectChat(2))
  saveConversationLabels('project-2',{...conversationLabels('project-2'),archived:true},conversationLabels('project-2'))
  render(<PixelConversationNavigation collapsed={false}/>)
  expect(screen.getByText('legacy-site')).toBeVisible()
  expect(screen.queryByText('Playground')).toBeNull()
  expect(within(screen.getByText('Pinned').closest('details')).getByRole('button',{name:'Build project 0',exact:true})).toBeVisible()
  expect(within(screen.getByText('Recent').closest('details')).queryByText('Build project 0')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Archived (1)'}))
  expect(screen.getByText('Playground')).toBeVisible()
  expect(screen.getByRole('button',{name:'Build project 2',exact:true})).toBeVisible()
  expect(screen.queryByText('Legacy project')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Show active conversations'}))
  expect(screen.getByText('legacy-site')).toBeVisible()
})

test('restores a file-only project after reload without publishing or duplicating its conversation',()=>{
  const task={schemaVersion:4,runId:'chatcmpl_11111111-1111-4111-8111-111111111111',
    startedAt:'2026-09-16T12:01:00.000Z',finishedAt:'2026-09-16T12:02:00.000Z',state:'completed',
    calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:null,
    projects:[{schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:'Playground/research-notes',observedAt:'2026-09-16T12:00:00.000Z'}]}
  saveConversation({schema:1,chatId:'files-only',messages:[{role:'user',content:'Write research notes'},
    {role:'assistant',content:'Saved the notes.',task}]})
  const first=render(<PixelConversationNavigation collapsed={false}/>)
  expect(screen.getByText('research-notes')).toBeVisible()
  const recent=screen.getByText('Recent').closest('details')
  expect(within(recent).queryByText('Write research notes')).toBeNull()
  expect(screen.getAllByRole('button',{name:'Write research notes',exact:true})).toHaveLength(1)
  first.unmount()
  render(<PixelConversationNavigation collapsed={false}/>)
  expect(screen.getByText('research-notes')).toBeVisible()
  expect(readConversations().find(chat=>chat.chatId==='files-only').messages[1].task.projects).toEqual(task.projects)
})
