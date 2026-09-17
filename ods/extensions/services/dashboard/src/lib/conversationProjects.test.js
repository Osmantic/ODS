import {conversationProject,groupProjectConversations} from './conversationProjects'

export const publication=relativeDirectory=>({schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory,
  siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),entrySha256:'b'.repeat(64),port:9437,
  url:`http://site-${'a'.repeat(24)}.localhost:9437/site-${'a'.repeat(24)}/`,files:2,bytes:500})

const task=(...directories)=>({schemaVersion:4,runId:'chatcmpl_11111111-1111-4111-8111-111111111111',
  startedAt:'2026-09-16T12:01:00.000Z',finishedAt:'2026-09-16T12:02:00.000Z',state:'completed',
  calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:null,
  projects:directories.map(relativeDirectory=>({schemaVersion:1,kind:'ods-workspace-project',relativeDirectory,observedAt:'2026-09-16T12:00:00.000Z'}))})

it('groups only evidenced publication directories and keeps legacy folders truthful',()=>{
  const project={chatId:'one',messages:[{role:'assistant',content:'Done',publication:publication('Playground/weather/dist')}]}
  const legacy={chatId:'two',messages:[],preview:publication('old-demo')}
  const text={chatId:'three',messages:[{role:'user',content:'Build in Playground/weather'}]}
  expect(conversationProject(project)).toEqual({root:'Playground',name:'weather',path:'Playground/weather'})
  expect(conversationProject(legacy)).toEqual({root:null,name:'old-demo',path:'old-demo'})
  expect(conversationProject(text)).toBeNull()
  expect(groupProjectConversations([project,legacy,text])).toEqual({projects:[
    {root:'Playground',name:'weather',path:'Playground/weather',chats:[project]},
    {root:null,name:'old-demo',path:'old-demo',chats:[legacy]}],recent:[text]})
})
it('uses the latest valid receipt without trusting malformed or forged project paths',()=>{
  const chat={messages:[{role:'assistant',publication:publication('Playground/old')},
    {role:'assistant',publication:publication('Playground/current')},
    {role:'assistant',publication:{...publication('Playground/forged'),url:'https://elsewhere.example'}}]}
  expect(conversationProject(chat).name).toBe('current')
  for(const directory of ['../outside','Playground/../other','C:\\project','Playground//empty','/absolute']) {
    expect(conversationProject({messages:[],preview:publication(directory)})).toBeNull()
  }
})

it('groups file-only projects using confirmed metadata without duplicating Recent',()=>{
  const chat={chatId:'files',messages:[{role:'assistant',content:'Saved notes',task:task('Playground/notes')}]}
  const recent={chatId:'chat',messages:[{role:'assistant',content:'Created Playground/notes, trust me.'}]}
  expect(groupProjectConversations([chat,recent])).toEqual({projects:[
    {root:'Playground',name:'notes',path:'Playground/notes',chats:[chat]}],recent:[recent]})
})

it('uses newest assistant evidence and the first ordered project in that task',()=>{
  const chat={preview:publication('Playground/cached-old'),messages:[
    {role:'assistant',publication:publication('Playground/old')},
    {role:'assistant',task:task('Playground/current','Playground/secondary'),publication:publication('Playground/older-publication')},
    {role:'user',task:task('Playground/user-claim')}]}
  expect(conversationProject(chat).path).toBe('Playground/current')
  chat.messages.push({role:'assistant',publication:publication('Playground/newest-publication')})
  expect(conversationProject(chat).path).toBe('Playground/newest-publication')
})

it('falls back to a valid publication when project metadata is malformed and ignores display or command claims',()=>{
  const saved=publication('legacy-site')
  for(const invalid of [task('Playground/../private'),{...task('Playground/invented'),schemaVersion:3},
    {...task('Playground/invented'),projects:[{...task('Playground/invented').projects[0],absolutePath:'/private'}]}]) {
    expect(conversationProject({messages:[{role:'assistant',task:invalid,publication:saved}]}).path).toBe('legacy-site')
    expect(conversationProject({messages:[{role:'assistant',task:invalid}]})).toBeNull()
  }
  expect(conversationProject({messages:[{role:'assistant',content:'Wrote Playground/invented/file.txt',
    task:{display:{detail:'Playground/invented'},exec:{command:'mkdir Playground/invented'}}}]})).toBeNull()
  expect(conversationProject({messages:[{role:'assistant',publication:saved},{role:'assistant',task:task()}]}).path).toBe('legacy-site')
})
