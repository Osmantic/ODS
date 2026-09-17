import {Blob as NodeBlob} from 'node:buffer'
import {parseProjectTasks} from './pixelTaskActivity'
import {teamMetadata,teamProjectTasks} from './portalTeams'
import {conversationProject} from './conversationProjects'
import {parseConversationImport} from './pixelConversationImport'
import {saveConversation,readConversations} from './pixelConversations'
import {exportConversation} from './pixelConversationExport'

const task=index=>({schemaVersion:4,runId:`chatcmpl_11111111-1111-4111-8111-${String(index).padStart(12,'0')}`,
  startedAt:'2026-09-16T12:00:00.000Z',finishedAt:'2026-09-16T12:02:00.000Z',state:'completed',
  calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:null,
  projects:[{schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:`Playground/project-${index}`,observedAt:`2026-09-16T12:01:0${index}.000Z`}]})
beforeEach(()=>localStorage.clear())
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})

it('retains at most six real observations ordered by newest project, without synthesizing a task',()=>{
  const tasks=Array.from({length:8},(_,index)=>task(index))
  const result=teamProjectTasks({agents:tasks.map(activity=>({activity}))},[tasks[0]])
  expect(result).toEqual(tasks.slice(2).reverse())
  expect(result[0]).toBe(tasks[7])
  expect(teamProjectTasks({agents:[]},result)).toEqual(result)
  expect(conversationProject({messages:[{role:'assistant',projectTasks:[tasks[0],tasks[7]]}]}).path).toBe('Playground/project-7')
})

it('rejects malformed, oversized and duplicate project collections at every persistence boundary',()=>{
  for(const projectTasks of [null,{},[task(1),task(1)],Array.from({length:7},(_,i)=>task(i)),
    [{...task(1),schemaVersion:3}],[{...task(1),projects:[]}],[{...task(1),secret:'raw command'}]]) {
    expect(parseProjectTasks(projectTasks)).toBeNull()
    const message={role:'assistant',content:'Saved',projectTasks}
    expect(teamMetadata(message)).not.toHaveProperty('projectTasks')
    expect(conversationProject({messages:[message]})).toBeNull()
    expect(()=>saveConversation({schema:1,chatId:'invalid',messages:[message]})).toThrow('Invalid project metadata')
    expect(()=>parseConversationImport({schemaVersion:1,kind:'ods-pixel-conversation',conversation:{schema:1,messages:[message]}})).toThrow('invalid project metadata')
  }
  expect(readConversations()).toEqual([])
})

it('round-trips independent project observations through export and import without reviving a team',async()=>{
  vi.stubGlobal('Blob',NodeBlob)
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:team-export')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(window.HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
  const projectTasks=[task(2),task(1)]
  saveConversation({schema:1,chatId:'team',messages:[{role:'assistant',content:'Team finished',teamId:'a'.repeat(32),projectTasks}]})
  exportConversation('team')
  const archive=JSON.parse(await URL.createObjectURL.mock.calls[0][0].text())
  const restored=parseConversationImport(archive)
  expect(restored.messages[0].projectTasks).toEqual(projectTasks)
  expect(restored.messages[0]).not.toHaveProperty('teamId')
  expect(restored.messages[0]).not.toHaveProperty('task')
  expect(restored).toMatchObject({inFlight:false,requestId:null,preview:null})
  saveConversation({...restored,chatId:'imported'})
  expect(conversationProject(readConversations().find(chat=>chat.chatId==='imported')).path).toBe('Playground/project-2')
})
