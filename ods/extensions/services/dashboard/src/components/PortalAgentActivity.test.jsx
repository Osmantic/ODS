import {act,fireEvent,render,screen,within} from '@testing-library/react'
import PortalAgentActivity,{QUIET_HINT} from './PortalAgentActivity'
import PortalActivityChange,{activityChangeRows} from './PortalActivityChange'
const start='2026-09-15T10:00:00.000Z',end='2026-09-15T10:00:05.000Z'
const display=(type,label,extra={})=>({type,label,detail:null,sources:[],steps:[],change:null,...extra})
const displays=[display('text','Conferindo a documentação.'),display('search','React reference',{sources:[{title:'Official docs',url:'https://react.dev/reference'}]}),display('tool','Reading a file',{detail:'app.jsx'}),display('steps','Check the result',{steps:[{id:'check',title:'Run checks',status:'completed'}]}),display('trace','Asking for your input')]
const task={schemaVersion:3,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:start,finishedAt:end,state:'completed',calls:5,failures:0,blocked:0,truncated:false,activities:[{kind:'unknown',calls:5,failures:0,blocked:0}],events:displays.map((display,i)=>({sequence:i+1,kind:'unknown',state:'completed',startedAt:start,finishedAt:end,display})),context:null,goal:null}
const call=(sequence,kind,state,value)=>({sequence,kind,state,startedAt:start,finishedAt:end,display:value})
const row=text=>screen.getByText(text).closest('li')
// No row, plan step or heading reads as broken: no alarm words, no alert icon.
const expectCalm=container=>{
 expect(screen.queryByText(/Failed|Blocked|Unconfirmed|Needs attention/)).toBeNull()
 expect(container.querySelector('.lucide-circle-alert')).toBeNull()
}
it('does not invent a completed duration from a persisted running receipt after reload',()=>{
 vi.useFakeTimers()
 try {
  vi.setSystemTime(new Date('2026-09-15T10:02:25.000Z'))
  const unfinished={...task,state:'running',finishedAt:null}
  const first=render(<PortalAgentActivity task={unfinished} active/> )
  first.rerender(<PortalAgentActivity task={unfinished}/>)
  expect(screen.getByRole('button',{name:'Activity recorded'})).toHaveAttribute('aria-expanded','false')
  expect(screen.queryByText(/^Worked for /)).toBeNull()
  first.unmount()
  vi.setSystemTime(new Date('2026-09-15T10:03:10.000Z'))
  const restored=render(<PortalAgentActivity task={JSON.parse(JSON.stringify(unfinished))}/>)
  expect(screen.getByRole('button',{name:'Activity recorded'})).toBeVisible()
  restored.rerender(<PortalAgentActivity task={task}/>)
  expect(screen.getByRole('button',{name:'Worked for 5s'})).toBeVisible()
  restored.unmount()
  vi.setSystemTime(new Date('2026-09-16T10:00:00.000Z'))
  render(<PortalAgentActivity task={JSON.parse(JSON.stringify(task))}/>)
  expect(screen.getByRole('button',{name:'Worked for 5s'})).toBeVisible()
 } finally {vi.useRealTimers()}
})
it('renders an empty file without crashing and distinguishes completed recovery from failed work',()=>{
 expect(activityChangeRows({kind:'patch',before:'',after:'--- old.txt\n+++ new.txt\n@@ -1 +1 @@\n--- heading\n+++ heading'})).toEqual([{type:'remove',text:'-- heading'},{type:'add',text:'++ heading'}]);
 render(<PortalActivityChange change={{file:'empty.txt',kind:'write',before:'',after:'',truncated:false}}/>);
 expect(screen.getByLabelText('Changes to empty.txt')).toHaveTextContent('Empty content');
 const {container}=render(<PortalAgentActivity task={{...task,failures:1,activities:[{kind:'unknown',calls:5,failures:1,blocked:0}],events:task.events.map((e,i)=>i===0?{...e,state:'failed'}:e)}}/>);
 expect(screen.getByRole('button',{name:'Worked for 5s'})).toHaveAttribute('aria-expanded','false');
 expectCalm(container);
});
it('says a long silent step is expected instead of leaving only a growing timer',()=>{
 vi.useFakeTimers()
 try {
  // The last step finished at 10:00:05; the model has been quiet since.
  const running={...task,state:'running',finishedAt:null}
  vi.setSystemTime(new Date('2026-09-15T10:00:30.000Z'))
  const early=render(<PortalAgentActivity task={running} active/>)
  expect(screen.queryByText(QUIET_HINT)).toBeNull()
  early.unmount()
  vi.setSystemTime(new Date('2026-09-15T10:01:00.000Z'))
  const quiet=render(<PortalAgentActivity task={running} active/>)
  expect(screen.getByRole('status')).toHaveTextContent(QUIET_HINT)
  expectCalm(quiet.container)
  quiet.unmount()
  // A running tool reports its own progress, and a finished reply never says it is still working.
  const tool=render(<PortalAgentActivity task={{...running,events:[call(1,'run','running',display('tool','Running a command'))]}} active/>)
  expect(screen.queryByText(QUIET_HINT)).toBeNull()
  tool.unmount()
  const done=render(<PortalAgentActivity task={task}/>)
  expect(screen.queryByText(QUIET_HINT)).toBeNull()
  done.unmount()
  // Before any step is recorded, the quiet stretch counts from when the reply started.
  render(<PortalAgentActivity active/>)
  expect(screen.queryByText(QUIET_HINT)).toBeNull()
  act(()=>{vi.advanceTimersByTime(46000)})
  expect(screen.getByRole('status')).toHaveTextContent(QUIET_HINT)
 } finally {vi.useRealTimers()}
})
it('shows a compact thinking line immediately with no fake steps or technical placeholder',()=>{
 render(<PortalAgentActivity active/>);
 expect(screen.getByRole('button',{name:/Thinking/})).toHaveAttribute('aria-expanded','true');
 expect(screen.queryByText(/No tool calls|Live activity|runtime started/)).toBeNull();
 expect(screen.queryAllByRole('listitem')).toHaveLength(0);
});
it('collapses completed work and expands the chronological mixed log with safe sources',()=>{
 const {rerender}=render(<PortalAgentActivity task={{...task,state:'running',finishedAt:null}} active/>);
 expect(screen.getAllByText('Conferindo a documentação.')).toHaveLength(2);
 expect(screen.getByRole('link',{name:/Official docs/})).toHaveAttribute('href','https://react.dev/reference');
 expect(new URL(screen.getByRole('link',{name:/Official docs/}).querySelector('img').src).searchParams.get('url')).toBe('https://react.dev');
 expect(screen.getByText('app.jsx')).toBeVisible();expect(screen.getByText('Run checks')).toBeVisible();
 rerender(<PortalAgentActivity task={task}/>);
 const trigger=screen.getByRole('button',{name:'Worked for 5s'});
 expect(trigger).toHaveAttribute('aria-expanded','false');expect(screen.queryByRole('link')).toBeNull();
 fireEvent.click(trigger);expect(screen.getByRole('link')).toBeVisible();
});
it('ends a reply that failed calmly and never turns unfinished work into a successful step',()=>{
 const value={...task,state:'failed',failures:1,activities:[{kind:'unknown',calls:5,failures:1,blocked:0}],events:task.events.map((event,i)=>i===4?{...event,state:'failed'}:i===2?{...event,state:'running',finishedAt:null}:event)};
 const {container}=render(<PortalAgentActivity task={value} status="error"/>);
 expect(screen.getByRole('button',{name:'Ended early · you can try again'})).toHaveAttribute('aria-expanded','true');
 // A call with no recorded end claims no result: no check and no duration.
 expect(row('Reading a file')).toHaveAttribute('data-state','unconfirmed');
 expect(row('Reading a file').querySelector('.portal-agent-row-meta')).toBeNull();
 // The last call did not work and nothing followed it, so it shows no status.
 expect(row('Asking for your input')).toHaveAttribute('data-state','tried');
 expect(row('Asking for your input').querySelector('.portal-agent-row-meta')).toBeNull();
 expectCalm(container);
});
it('shows calls that did not work as ordinary steps while the agent keeps going',()=>{
 const events=[call(1,'run','failed',display('tool','Running a command',{detail:'npm test'})),call(2,'unknown','blocked',null),call(3,'edit','failed',display('tool','Editing a file',{detail:'app.js'}))]
 const activities=edits=>[{kind:'run',calls:1,failures:1,blocked:0},{kind:'edit',calls:edits,failures:1,blocked:0},{kind:'unknown',calls:1,failures:1,blocked:1}]
 const working={...task,state:'running',finishedAt:null,calls:3,failures:3,blocked:1,activities:activities(1),events}
 const {container,rerender}=render(<PortalAgentActivity task={working} active/>);
 expect(within(row('npm test')).getByText('Tried another way')).toBeVisible();
 // Portal declined this call before it ran; the agent was told why and continued.
 expect(within(row('Using a tool')).getByText('Tried another way')).toBeVisible();
 // The latest call has no outcome to show while the agent decides what to do next.
 expect(row('app.js').querySelector('.portal-agent-row-meta')).toBeNull();
 expectCalm(container);
 rerender(<PortalAgentActivity task={{...working,calls:4,activities:activities(2),events:[...events,call(4,'edit','completed',display('tool','Editing a file',{detail:'index.js'}))]}} active/>);
 expect(within(row('app.js')).getByText('Tried another way')).toBeVisible();
 expect(within(row('index.js')).getByLabelText('Finished')).toBeInTheDocument();
 expectCalm(container);
});
it('keeps a delivered answer calm when its receipt recorded a failed run',()=>{
 const value={...task,state:'failed'}
 const {rerender}=render(<PortalAgentActivity task={value} status="done"/>);
 expect(screen.getByRole('button',{name:'Worked for 5s'})).toHaveAttribute('aria-expanded','false');
 rerender(<PortalAgentActivity task={value}/>);
 expect(screen.getByRole('button',{name:'Worked for 5s'})).toHaveAttribute('aria-expanded','false');
 expect(screen.queryByText(/Needs attention|Ended early/)).toBeNull();
});
it('pauses a plan step the agent reported as blocked instead of raising an alert',()=>{
 const plan=display('steps','Website plan',{steps:[{id:'build',title:'Build the page',status:'completed'},{id:'key',title:'Get an API key',status:'blocked'},{id:'check',title:'Check the result',status:'pending'}]})
 const value={...task,calls:1,activities:[{kind:'unknown',calls:1,failures:0,blocked:0}],events:[call(1,'unknown','completed',plan)]}
 const {container}=render(<PortalAgentActivity task={value} active/>);
 expect(row('Get an API key')).toHaveAttribute('data-state','paused');
 expect(within(row('Get an API key')).getByLabelText('Paused')).toBeInTheDocument();
 expect(within(row('Check the result')).getByLabelText('Pending')).toBeInTheDocument();
 expect(screen.queryByLabelText('Blocked')).toBeNull();
 expectCalm(container);
});
it('groups older receipts by kind without marking a kind with working calls as failed',()=>{
 const grouped={schemaVersion:1,runId:task.runId,startedAt:start,finishedAt:end,state:'completed',calls:6,failures:3,blocked:1,truncated:false,activities:[{kind:'read',calls:2,failures:2,blocked:1},{kind:'run',calls:4,failures:1,blocked:0}]}
 const {container}=render(<PortalAgentActivity task={grouped} status="done"/>);
 fireEvent.click(screen.getByRole('button',{name:'Worked for 5s'}));
 expect(within(row('Reading files')).getByText('Tried another way')).toBeVisible();
 expect(within(row('Ran')).getByLabelText('Finished')).toBeInTheDocument();
 expectCalm(container);
});
it('opens actual file edits and command details without claiming failed edits succeeded',()=>{
 const change={file:'app.js',kind:'edit',before:'const n = 1;\nunchanged',after:'const n = 2;\nunchanged',truncated:false}
 expect(activityChangeRows(change)).toEqual([{type:'remove',text:'const n = 1;'},{type:'add',text:'const n = 2;'},{type:'context',text:'unchanged'}]);
 const value={...task,events:task.events.map((event,i)=>i===2?{...event,display:display('tool','Editing a file',{detail:'app.js',change})}:event)}
 const {rerender}=render(<PortalAgentActivity task={value} active/>);
 fireEvent.click(screen.getByRole('button',{name:/Edited/}));
 expect(screen.getByLabelText('Changes to app.js')).toHaveTextContent('const n = 2;');
 expect(screen.getAllByLabelText('1 lines added, 1 lines removed')).toHaveLength(2);
 rerender(<PortalAgentActivity task={{...value,events:value.events.map((event,i)=>i===2?{...event,state:'failed'}:event)}} active/>);
 expect(screen.queryByLabelText('Changes to app.js')).toBeNull();
 expect(within(row('Editing a file')).getByText('Tried another way')).toBeVisible();
});
