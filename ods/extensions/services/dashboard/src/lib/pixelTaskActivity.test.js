import {parseTaskActivity,parseTaskActivityFrame} from './pixelTaskActivity'

const runId='chatcmpl_11111111-1111-4111-8111-111111111111'
const project=(relativeDirectory='Playground/notes')=>({schemaVersion:1,kind:'ods-workspace-project',relativeDirectory,observedAt:'2026-09-16T12:00:00.000Z'})
const task=()=>({schemaVersion:4,runId,startedAt:'2026-09-16T12:01:00.000Z',finishedAt:'2026-09-16T12:02:00.000Z',state:'completed',
  calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'edit',calls:1,failures:0,blocked:0}],
  events:[{sequence:1,kind:'edit',state:'completed',startedAt:'2026-09-16T12:01:00.000Z',finishedAt:'2026-09-16T12:02:00.000Z',display:null}],
  context:null,goal:null,projects:[project()]})

it('accepts confirmed file-only projects and earlier durable observations in version 4 streams',()=>{
  const saved=task()
  expect(parseTaskActivity(saved,runId)).toBe(saved)
  expect(parseTaskActivityFrame({id:runId,pixel_task:saved,choices:[{finish_reason:'stop'}]})).toBe(saved)
  const live={...saved,state:'running',finishedAt:null}
  expect(parseTaskActivityFrame({object:'ods.task.activity',id:runId,pixel_task:live})).toBe(live)
})

it.each([1,2,3])('preserves version %i without accepting project metadata added to an older schema',schemaVersion=>{
  const saved={...task(),schemaVersion}
  delete saved.projects
  if(schemaVersion===1){delete saved.events;delete saved.context;delete saved.goal}
  if(schemaVersion===2)for(const event of saved.events)delete event.display
  expect(parseTaskActivity(saved,runId)).toBe(saved)
  expect(parseTaskActivity({...saved,projects:[project()]},runId)).toBeNull()
})

it.each(['Playground/../secret','Playground/name/files','Playground//name','/Playground/name','playground/name','C:\\name',
  'Playground/name.','Playground/CON','Playground/con.txt','Playground/PRN','Playground/AUX.log','Playground/nul',
  'Playground/COM1','Playground/COM9.foo','Playground/LPT1','Playground/lpt9.txt','Playground/área',`Playground/${'a'.repeat(65)}`,
  'Playground/with space','Playground/.hidden'])('rejects unsafe workspace project directory %s',relativeDirectory=>{
  expect(parseTaskActivity({...task(),projects:[project(relativeDirectory)]},runId)).toBeNull()
})

it.each(['Playground/CONsole','Playground/COM10','Playground/notes.v2','Playground/project-name_2',`Playground/${'a'.repeat(64)}`])('keeps portable valid project names %s',relativeDirectory=>{
  const saved={...task(),projects:[project(relativeDirectory)]}
  expect(parseTaskActivity(saved,runId)).toBe(saved)
})

it('rejects duplicate, oversized, malformed or excessive project metadata',()=>{
  const saved=task()
  for(const projects of [null,{},[project(),project()],Array.from({length:9},(_,i)=>project(`Playground/p${i}`)),
    [{...project(),schemaVersion:2}],[{...project(),kind:'model-claim'}],[{...project(),absolutePath:'/private'}],
    [{...project(),observedAt:'2026-09-16T12:02:00Z'}],[{...project(),observedAt:'2026-02-30T12:00:00.000Z'}],
    [{...project(),observedAt:'2026-09-16T12:02:00.001Z'}]]) {
    expect(parseTaskActivity({...saved,projects},runId)).toBeNull()
  }
  expect(parseTaskActivity({...saved,projects:[]},runId)).toBeTruthy()
  expect(parseTaskActivity({...saved,projects:Array.from({length:8},(_,i)=>project(`Playground/p${i}`))},runId)).toBeTruthy()
})

it('retains the closed public event display contract for version 4',()=>{
  const saved=task()
  saved.events[0].display={type:'tool',label:'Wrote a file',detail:'notes.md',sources:[],steps:[],change:null}
  expect(parseTaskActivity(saved,runId)).toBe(saved)
  saved.events[0].display.command='private command'
  expect(parseTaskActivity(saved,runId)).toBeNull()
})
