import {fireEvent,render,screen} from '@testing-library/react'
import PortalAgentActivity from './PortalAgentActivity'
import PortalActivityChange,{activityChangeRows} from './PortalActivityChange'
const start='2026-09-15T10:00:00.000Z',end='2026-09-15T10:00:05.000Z'
const display=(type,label,extra={})=>({type,label,detail:null,sources:[],steps:[],change:null,...extra})
const displays=[display('text','Conferindo a documentação.'),display('search','React reference',{sources:[{title:'Official docs',url:'https://react.dev/reference'}]}),display('tool','Reading a file',{detail:'app.jsx'}),display('steps','Check the result',{steps:[{id:'check',title:'Run checks',status:'completed'}]}),display('trace','Asking for your input')]
const task={schemaVersion:3,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:start,finishedAt:end,state:'completed',calls:5,failures:0,blocked:0,truncated:false,activities:[{kind:'unknown',calls:5,failures:0,blocked:0}],events:displays.map((display,i)=>({sequence:i+1,kind:'unknown',state:'completed',startedAt:start,finishedAt:end,display})),context:null,goal:null}
it('renders an empty file without crashing and distinguishes completed recovery from failed work',()=>{
 expect(activityChangeRows({kind:'patch',before:'',after:'--- old.txt\n+++ new.txt\n@@ -1 +1 @@\n--- heading\n+++ heading'})).toEqual([{type:'remove',text:'-- heading'},{type:'add',text:'++ heading'}]);
 render(<PortalActivityChange change={{file:'empty.txt',kind:'write',before:'',after:'',truncated:false}}/>);
 expect(screen.getByLabelText('Changes to empty.txt')).toHaveTextContent('Empty content');
 render(<PortalAgentActivity task={{...task,failures:1,activities:[{kind:'unknown',calls:5,failures:1,blocked:0}],events:task.events.map((e,i)=>i===0?{...e,state:'failed'}:e)}}/>);
 expect(screen.getByRole('button',{name:'Worked for 5s'})).toHaveAttribute('aria-expanded','false');
});
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
it('keeps failures visible and never turns unfinished work into a successful step',()=>{
 const value={...task,state:'failed',failures:1,activities:[{kind:'unknown',calls:5,failures:1,blocked:0}],events:task.events.map((event,i)=>i===4?{...event,state:'failed'}:i===2?{...event,state:'running',finishedAt:null}:event)};
 render(<PortalAgentActivity task={value} status="error"/>);
 expect(screen.getByRole('button',{name:'Needs attention'})).toHaveAttribute('aria-expanded','true');
 expect(screen.getByText('Failed')).toBeVisible();expect(screen.getByText('Unconfirmed')).toBeVisible();
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
});
