// Portal implementation inspired by AI CSS Thinking + Reasoning and beUI
// Agent Activity. Uses real public events rather than their demo timelines.
import {useEffect, useId, useLayoutEffect, useRef, useState} from 'react'
import {Check, ChevronDown, Circle, FileSearch, FilePenLine, Globe2, Terminal, AlertCircle, Layers, MessageSquare, Wrench} from 'lucide-react'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import {activityProgressRows} from '../lib/portalActivityProgress'
import PortalActivityChange, {ActivityChangeCounts} from './PortalActivityChange'
import PortalSiteIcon from './PortalSiteIcon'
import './portal-agent-activity.css'

const labels={read:'Reading files',run:'Running a command',edit:'Editing files',browser:'Browsing the web',preview:'Publishing a preview',action:'Running an operation',agent:'Coordinating agents',unknown:'Using a tool'}
const icons={read:FileSearch,run:Terminal,edit:FilePenLine,browser:Globe2,preview:Layers,agent:MessageSquare}
const completedLabels={'Reading a file':'Read','Writing a file':'Wrote','Editing a file':'Edited','Applying changes':'Applied changes','Running a command':'Ran','Finding available tools':'Found available tools','Checking tool parameters':'Checked tool parameters'}
export function activityDuration(seconds) {
  const n=Math.max(0,Math.round(seconds))
  return n<60?`${n}s`:`${Math.floor(n/60)}m${n%60?` ${n%60}s`:''}`
}
function ActivityRow({event,active}) {
  const [expanded,setExpanded]=useState(false)
  const display=event.display, type=display?.type || 'tool'
  const state=event.state==='running' && !active ? 'unconfirmed' : event.state
  const rawLabel=display?.label || labels[event.kind]
  const label=state==='completed'?(completedLabels[rawLabel] || rawLabel):rawLabel
  const Icon=type==='trace'?MessageSquare:icons[event.kind] || Wrench
  const statusLabel={running:'In progress',completed:'Finished',failed:'Failed',blocked:'Blocked',unconfirmed:'Unconfirmed'}[state]
  const seconds=event.finishedAt ? (Date.parse(event.finishedAt)-Date.parse(event.startedAt))/1000 : null
  const change=state==='completed'?display?.change:null
  const expandable=!!change || !!display?.detail
  return <li className={`portal-agent-row is-${type}`} data-state={state}>
    <div className="portal-agent-row-heading">
      {type!=='text' && <Icon size={14} aria-hidden="true"/>}
      {expandable?<button type="button" className="portal-agent-row-label portal-agent-row-toggle" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}><span className="portal-agent-action-label">{label}</span>{(change?.file || display.detail) && <span className="portal-agent-target"> {change?.file || display.detail}</span>}<ActivityChangeCounts change={change}/><ChevronDown size={12} aria-hidden="true"/></button>:<span className="portal-agent-row-label">{label}</span>}
      {type!=='text' && <span className="portal-agent-row-meta" title={statusLabel}>
        {state==='running'?<span className="portal-agent-dot" aria-label={statusLabel}/>:['failed','blocked','unconfirmed'].includes(state)?<><AlertCircle size={12}/><span>{statusLabel}</span></>:<><Check size={12} aria-label={statusLabel}/>{seconds!==null && <span>{activityDuration(seconds)}</span>}</>}
      </span>}
    </div>
    {type==='text' && ['failed','blocked','unconfirmed'].includes(state) && <small>{statusLabel}</small>}
    {expanded && (change?<PortalActivityChange change={change}/>:<pre className="portal-agent-detail">{display.detail}</pre>)}
    {!!display?.sources.length && <ul className="portal-agent-sources" aria-label="Sources from this tool">{display.sources.map(source=><li key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer"><PortalSiteIcon href={source.url}/><span>{source.title}</span><small>{new URL(source.url).hostname}</small></a></li>)}</ul>}
    {!!display?.steps.length && <ol className="portal-agent-plan" aria-label="Reported plan steps">{display.steps.map(step=><li key={step.id} data-state={step.status}>{step.status==='completed'?<Check size={13} aria-label="Completed"/>:step.status==='running'?<span className="portal-agent-dot" aria-label="In progress"/>:step.status==='blocked'?<AlertCircle size={13} aria-label="Blocked"/>:<Circle size={12} aria-label="Pending"/>}<span>{step.title}</span></li>)}</ol>}
  </li>
}

export default function PortalAgentActivity({task:raw,active=false,status}) {
  const task=parseTaskActivity(raw,raw?.runId)
  const id=useId(), viewport=useRef(null), content=useRef(null), follow=useRef(true), mountedAt=useRef(Date.now())
  const failed=status==='error' || task?.state==='failed'
  const stopped=status==='stopped'
  const [open,setOpen]=useState(active||failed), [now,setNow]=useState(Date.now()), [fade,setFade]=useState({top:false,bottom:false})
  const events=task?.events || task?.activities.map((row,i)=>({sequence:i+1,kind:row.kind,state:row.blocked?'blocked':row.failures?'failed':active?'running':'completed',startedAt:task.startedAt,finishedAt:null})) || []
  const progressRows=activityProgressRows(events,typeof navigator==='undefined'?'en':navigator.language)
  const start=task?.startedAt?Date.parse(task.startedAt):mountedAt.current
  const seconds=((task?.finishedAt?Date.parse(task.finishedAt):now)-start)/1000
  const current=[...events].reverse().find(event=>event.state==='running')
  const currentType=current?.display?.type
  const progress=[...events].reverse().find(event=>event.display?.type==='text' && event.state==='completed')?.display?.label
  const activeLabel=progress || (currentType==='search' || current?.kind==='browser'?'Searching the web…':currentType==='text' || !current?'Thinking…':currentType==='steps'?'Planning…':'Working…')
  const label=active?activeLabel:stopped?'Stopped':failed?'Needs attention':`Worked for ${activityDuration(seconds)}`
  const refreshFade=()=>{const el=viewport.current;if(el)setFade({top:el.scrollTop>2,bottom:el.scrollTop+el.clientHeight<el.scrollHeight-2})}
  useEffect(()=>{
    if(!active)return
    const timer=setInterval(()=>setNow(Date.now()),1000)
    return ()=>clearInterval(timer)
  },[active])
  useEffect(()=>{setOpen(active||failed||stopped);follow.current=true},[active,failed,stopped,task?.runId])
  useLayoutEffect(()=>{
    const el=viewport.current
    if(!el)return
    const measure=()=>{if(active && follow.current)el.scrollTop=el.scrollHeight;refreshFade()}
    measure()
    if(typeof ResizeObserver==='undefined' || !content.current)return
    const observer=new ResizeObserver(measure);observer.observe(content.current)
    return ()=>observer.disconnect()
  },[task,active,open])
  if(!task && !active)return null
  return <section className="portal-agent-activity" aria-label="Agent activity" data-active={active}>
    <button type="button" id={`${id}-trigger`} className="portal-agent-disclosure" aria-expanded={open} aria-controls={`${id}-log`} onClick={()=>{
      setOpen(value=>!value);if(!open && viewport.current){viewport.current.scrollTop=0;follow.current=false;refreshFade()}
    }}>
      <span className={active?'portal-agent-shimmer':''}>{label}</span>
      {active && seconds>=1 && <span className="portal-agent-time">{activityDuration(seconds)}</span>}
      <ChevronDown size={13} aria-hidden="true"/>
    </button>
    <div id={`${id}-log`} role="region" aria-labelledby={`${id}-trigger`} hidden={!open}>
      <div ref={viewport} className="portal-agent-viewport" tabIndex={events.length?0:undefined} aria-label="Activity history" data-fade-top={fade.top} data-fade-bottom={fade.bottom} onScroll={()=>{const el=viewport.current;follow.current=el.scrollTop+el.clientHeight>=el.scrollHeight-12;refreshFade()}}>
        <ol ref={content} className="portal-agent-stream" aria-label="Execution steps">{progressRows.map(row=>row.event?<ActivityRow key={row.event.sequence} event={row.event} active={active}/>:<li key={row.id} className="portal-agent-progress"><p>{row.text}</p></li>)}</ol>
        {!events.length && !active && <p className="portal-agent-empty">No tool activity was recorded for this response.</p>}
        {task?.calls>events.length && <p className="portal-agent-empty">Showing the latest {events.length} of {task.calls} recorded actions.</p>}
      </div>
      {active && !follow.current && fade.bottom && <button type="button" className="portal-agent-follow" onClick={()=>{follow.current=true;if(viewport.current)viewport.current.scrollTop=viewport.current.scrollHeight;refreshFade()}}>Follow latest</button>}
    </div>
  </section>
}
