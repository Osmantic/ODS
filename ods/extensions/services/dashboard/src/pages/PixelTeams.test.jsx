// Team recovery uses a synthetic receipt; origin handshake has dedicated transport/browser coverage.
vi.mock('../lib/useVerifiedPreview',()=>({default:(_preview,access)=>access}))
import {fireEvent,screen,waitFor} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'
import * as portalTeams from '../lib/portalTeams'
import {saveConversation,readConversations,SELECT_EVENT} from '../lib/pixelConversations'
import {conversationProject} from '../lib/conversationProjects'

beforeEach(()=>{localStorage.clear();sessionStorage.clear()})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

it('delivers a confirmed team preview, persists it and does not reopen it on every poll',async()=>{
  const id='c'.repeat(32),sha256='a'.repeat(64),siteId=`site-${sha256.slice(0,24)}`
  const publication={schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'Playground/snake',
    siteId,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,files:3,bytes:1000,sha256,entrySha256:'b'.repeat(64)}
  const agent={id:'0',name:'Builder',role:'builder',status:'completed',conversation:[{role:'assistant',content:'Published game'}],publication}
  const team={id,status:'completed',goal:'Build game',agents:[agent]}
  saveConversation({schema:1,chatId:'team-preview',messages:[{role:'user',content:'Build game'},{role:'assistant',teamId:id,content:'Working'}]})
  let controller={teams:[team],busy:false,error:'',selected:null,select:vi.fn()}
  vi.spyOn(portalTeams,'usePortalTeams').mockImplementation(()=>controller)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true})})))
  const view=render(<Pixel/> )
  expect(await screen.findByTitle('Interactive Portal preview')).toHaveAttribute('src',`/pixel-preview/${siteId}/__ods_view__.html`)
  await waitFor(()=>expect(readConversations()[0].messages[1].publication).toEqual(publication))
  fireEvent.click(screen.getByRole('button',{name:'Close preview',exact:true}))
  controller={...controller,teams:[{...team}]}
  view.rerender(<Pixel/> )
  expect(screen.queryByTitle('Interactive Portal preview')).toBeNull()
  view.unmount()
  render(<Pixel/> )
  expect(screen.queryByTitle('Interactive Portal preview')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
  expect(await screen.findByTitle('Interactive Portal preview')).toBeVisible()
  expect(readConversations()[0].messages[1].publication).toEqual(publication)
})

it('does not pop the workspace open when the user switches to a chat whose team already published',async()=>{
  const id='e'.repeat(32),sha256='a'.repeat(64),siteId=`site-${sha256.slice(0,24)}`
  const publication={schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'Playground/snake',
    siteId,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,files:3,bytes:1000,sha256,entrySha256:'b'.repeat(64)}
  const team={id,status:'completed',goal:'Build game',agents:[{id:'0',name:'Builder',role:'builder',status:'completed',conversation:[],publication}]}
  const now=vi.spyOn(Date,'now')
  now.mockReturnValue(1000)
  saveConversation({schema:1,chatId:'published-chat',messages:[{role:'user',content:'Build game'},{role:'assistant',teamId:id,content:'Done',publication}]})
  now.mockReturnValue(2000)
  saveConversation({schema:1,chatId:'current-chat',messages:[{role:'user',content:'Hello'},{role:'assistant',content:'Hi'}]})
  vi.spyOn(portalTeams,'usePortalTeams').mockReturnValue({teams:[team],busy:false,error:'',selected:null,select:vi.fn()})
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true})})))
  render(<Pixel/> )
  await screen.findByText('Available')
  window.dispatchEvent(new CustomEvent(SELECT_EVENT,{detail:'published-chat'}))
  await screen.findByText('Done')
  expect(screen.queryByTitle('Interactive Portal preview')).toBeNull()
})

it.each(['failed','running','malformed'])('does not promote an unconfirmed team preview (%s)',async state=>{
  const id='d'.repeat(32),sha256='a'.repeat(64),siteId=`site-${sha256.slice(0,24)}`
  const publication={schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',siteId,
    port:9437,url:state==='malformed'?'https://untrusted.example/':`http://${siteId}.localhost:9437/${siteId}/`,files:1,bytes:1,sha256,entrySha256:'b'.repeat(64)}
  saveConversation({schema:1,chatId:'unconfirmed-preview',messages:[{role:'user',content:'Build game'},{role:'assistant',teamId:id,content:'Working'}]})
  vi.spyOn(portalTeams,'usePortalTeams').mockReturnValue({teams:[{id,status:state==='malformed'?'completed':state,agents:[{id:'0',name:'Builder',role:'builder',status:state==='malformed'?'completed':state,publication,conversation:[]}]}],busy:false,error:'',selected:null,select:vi.fn()})
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true})})))
  render(<Pixel/> )
  await screen.findByText('Available')
  expect(screen.queryByTitle('Interactive Portal preview')).toBeNull()
  expect(readConversations()[0].messages[1].publication).toBeUndefined()
})

it('persists team project observations when the summary stays identical and restores them after reload',async()=>{
  const id='a'.repeat(32)
  const activity={schemaVersion:4,runId:'chatcmpl_11111111-1111-4111-8111-111111111111',
    startedAt:'2026-09-16T12:00:00.000Z',finishedAt:'2026-09-16T12:01:00.000Z',state:'completed',
    calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:null,
    projects:[{schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:'Playground/team-notes',observedAt:'2026-09-16T12:00:30.000Z'}]}
  const team={id,status:'completed',goal:'Write notes',agents:[{id:'0',name:'Writer',role:'builder',task:'Write notes',status:'completed',conversation:[{role:'assistant',content:'Saved the notes.'}]}]}
  const content=portalTeams.teamSummary(team)
  saveConversation({schema:1,chatId:'team-project',messages:[{role:'user',content:'Write notes'},{role:'assistant',teamId:id,content}]})
  let controller={teams:[team],busy:false,error:'',selected:null,select:vi.fn()}
  vi.spyOn(portalTeams,'usePortalTeams').mockImplementation(()=>controller)
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:true,model:'pixel/default'})})))
  const view=render(<Pixel/>);await screen.findByText('Available')
  expect(readConversations()[0].messages[1]).not.toHaveProperty('projectTasks')
  controller={...controller,teams:[{...team,agents:[{...team.agents[0],activity}]}]}
  view.rerender(<Pixel/>)
  await waitFor(()=>expect(readConversations()[0].messages[1].projectTasks).toEqual([activity]))
  expect(readConversations()[0].messages[1].content).toBe(content)
  view.unmount()
  controller={...controller,teams:[]}
  render(<Pixel/>)
  await screen.findByText('Available')
  await waitFor(()=>expect(readConversations()[0].messages[1].projectTasks).toEqual([activity]))
  expect(conversationProject(readConversations()[0]).path).toBe('Playground/team-notes')
})
it('turns /agents into a conversational mode and asks the backend to plan without a numeric argument',async()=>{
  let team
  const fetcher=vi.fn(async(url,options)=>{
    const body=options?.body && JSON.parse(options.body)
    let data={available:true,model:'pixel/default'}
    if(url.endsWith('/agents/list'))data={teams:team?[team]:[]}
    if(url.endsWith('/agents/start')){
      team={id:'b'.repeat(32),request_id:body.request_id,goal:body.task,status:'running',agents:[{id:'0',name:'Coordinator',role:'coordinator',task:'Plan the team',status:'running',turn:0,conversation:[]}]};data=team
    }
    return {ok:true,json:async()=>data}
  })
  vi.stubGlobal('fetch',fetcher)
  render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'/agents'}})
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('')
  expect(screen.getByRole('button',{name:'Send',exact:true})).toBeDisabled()
  expect(screen.queryByRole('combobox',{name:'Number of agents'})).not.toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Escreva e revise um anúncio curto'}})
  fireEvent.click(screen.getByRole('button',{name:'Send',exact:true}))
  expect(await screen.findByRole('tab',{name:'Subagents'})).toHaveAttribute('aria-selected','true')
  expect(screen.getByRole('tabpanel',{name:'Subagents'})).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  const payload=JSON.parse(fetcher.mock.calls.find(([url])=>url.endsWith('/agents/start'))[1].body)
  expect(payload.task).toBe('Escreva e revise um anúncio curto')
  expect(payload).not.toHaveProperty('count')
  await waitFor(()=>expect(screen.getByText('Portal is planning the team…')).toBeInTheDocument())
  expect(fetcher.mock.calls.some(([url])=>url.includes('/chat/stream'))).toBe(false)
  fireEvent.click(screen.getByRole('button',{name:'Close Subagents'}))
  fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
  fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
  expect(screen.queryByRole('tab',{name:'Subagents'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'View subagents'}))
  expect(screen.getByRole('tabpanel',{name:'Subagents'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'New chat'}))
  fireEvent.click(screen.getByRole('button',{name:'Workspace',exact:true}))
  expect(screen.queryByRole('tab',{name:'Subagents'})).toBeNull()
  expect(screen.queryByText('Escreva e revise um anúncio curto')).toBeNull()
})

it('runs /goal through the durable controller, answers inline and exposes stop',async()=>{
  let team
  const time='2026-09-15T10:00:00.000Z'
  const activity={schemaVersion:2,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:time,finishedAt:time,state:'finished',calls:0,failures:0,blocked:0,truncated:false,activities:[],events:[],context:null,goal:{status:'waiting',summary:'Choose a style',steps:[{id:'work',title:'Create the proposal',status:'pending'}]}}
  const fetcher=vi.fn(async(url,options)=>{
    const body=options?.body && JSON.parse(options.body)
    let data={available:true,model:'pixel/default'}
    if(url.endsWith('/agents/list'))data={teams:team?[team]:[]}
    if(url.endsWith('/agents/start')) {
      team={id:'b'.repeat(32),request_id:body.request_id,goal:body.task,mode:'goal',status:'waiting',agents:[{id:'0',name:'Builder',role:'builder',task:'Do the work',status:'waiting',turn:0,activity,conversation:[],questions:[{id:'style',question:'Qual estilo?',options:['Clean','Colorido']}]}]};data=team
    }
    if(url.endsWith('/agents/answer')){team={...team,status:'running',agents:[{...team.agents[0],status:'running',questions:null}]};data=team}
    if(url.endsWith('/agents/stop')){team={...team,status:'cancelled',agents:[{...team.agents[0],status:'cancelled'}]};data=team}
    return {ok:true,json:async()=>data}
  })
  vi.stubGlobal('fetch',fetcher)
  const view=render(<Pixel/>);await screen.findByText('Available')
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'/'}})
  fireEvent.click(screen.getByRole('button',{name:'Goal Plan, work and verify the outcome'}))
  expect(screen.getByPlaceholderText('Message Portal...')).toHaveValue('')
  expect(screen.getByRole('button',{name:'Send',exact:true})).toBeDisabled()
  fireEvent.change(screen.getByPlaceholderText('Message Portal...'),{target:{value:'Escreva uma proposta'}})
  fireEvent.click(screen.getByRole('button',{name:'Send',exact:true}))
  await screen.findByRole('region',{name:'Questions for you'})
  expect(screen.queryByRole('dialog')).toBeNull()
  const payload=JSON.parse(fetcher.mock.calls.find(([url])=>url.endsWith('/agents/start'))[1].body)
  expect(payload).toMatchObject({mode:'goal',task:'Escreva uma proposta'})
  fireEvent.click(screen.getByRole('radio',{name:/Clean/}))
  fireEvent.click(screen.getByRole('button',{name:'Continue',exact:true}))
  await waitFor(()=>expect(fetcher.mock.calls.some(([url])=>url.endsWith('/agents/answer'))).toBe(true))
  const answer=JSON.parse(fetcher.mock.calls.find(([url])=>url.endsWith('/agents/answer'))[1].body)
  expect(answer.answers).toEqual({style:'Clean'})
  view.unmount()
  render(<Pixel/>)
  await screen.findByRole('button',{name:'View goal history'})
  fireEvent.click((await screen.findAllByRole('button',{name:'Stop goal'}))[0])
  await waitFor(()=>expect(fetcher.mock.calls.some(([url])=>url.endsWith('/agents/stop'))).toBe(true))
  expect(fetcher.mock.calls.filter(([url])=>url.endsWith('/agents/start'))).toHaveLength(1)
  expect(fetcher.mock.calls.some(([url])=>url.includes('/chat/stream'))).toBe(false)
})
