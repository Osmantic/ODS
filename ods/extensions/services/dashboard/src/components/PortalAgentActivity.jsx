// Portal implementation inspired by AI CSS Thinking + Reasoning and beUI
// Agent Activity. Uses real public events rather than their demo timelines.
import {useEffect, useId, useLayoutEffect, useRef, useState} from 'react'
import {Check, ChevronDown, Circle, FileSearch, FilePenLine, Globe2, Terminal, Layers, MessageSquare, PauseCircle, Wrench} from 'lucide-react'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import {activityProgressRows} from '../lib/portalActivityProgress'
import PortalActivityChange, {ActivityChangeCounts} from './PortalActivityChange'
import PortalSiteIcon from './PortalSiteIcon'
import './portal-agent-activity.css'

const labels={read:'Reading files',run:'Running a command',edit:'Editing files',browser:'Browsing the web',preview:'Publishing a preview',action:'Running an operation',agent:'Coordinating agents',unknown:'Using a tool'}
const icons={read:FileSearch,run:Terminal,edit:FilePenLine,browser:Globe2,preview:Layers,agent:MessageSquare}
const completedLabels={'Reading a file':'Read','Writing a file':'Wrote','Editing a file':'Edited','Applying changes':'Applied changes','Running a command':'Ran','Finding available tools':'Found available tools','Checking tool parameters':'Checked tool parameters'}
// A model step shows nothing until it is complete (the router validates tool
// calls before they stream), so writing a whole page reads as one long, silent
// "Thinking…". After this quiet stretch, say that it is expected.
const QUIET_HINT_MS=45000
export const QUIET_HINT='Still working. Bigger steps, like writing a whole page, can take a couple of minutes.'
export function activityDuration(seconds) {
  const n=Math.max(0,Math.round(seconds))
  return n<60?`${n}s`:`${Math.floor(n/60)}m${n%60?` ${n%60}s`:''}`
}
// Receipt states are telemetry, not verdicts. A call that returned an error
// ('failed') or that Portal declined before it ran ('blocked') is ordinary
// agent work: the agent is told why and continues, so neither marks a step as
// broken. A call whose end was never recorded ('running' after the response)
// claims no result either way.
function rowState(event,active) {
  if(event.state==='running')return active?'running':'unconfirmed'
  return ['failed','blocked'].includes(event.state)?'tried':event.state
}

function ActivityRow({event,active,later}) {
  const [expanded,setExpanded]=useState(false)
  const display=event.display, type=display?.type || 'tool'
  const state=rowState(event,active)
  const rawLabel=display?.label || labels[event.kind]
  const label=state==='completed'?(completedLabels[rawLabel] || rawLabel):rawLabel
  const Icon=type==='trace'?MessageSquare:icons[event.kind] || Wrench
  // A tried call says so only once a later step shows the agent moved on.
  const statusLabel={running:'In progress',completed:'Finished',tried:later?'Tried another way':null}[state]
  const seconds=event.finishedAt ? (Date.parse(event.finishedAt)-Date.parse(event.startedAt))/1000 : null
  const change=state==='completed'?display?.change:null
  const expandable=!!change || !!display?.detail
  return <li className={`portal-agent-row is-${type}`} data-state={state}>
    <div className="portal-agent-row-heading">
      {type!=='text' && <Icon size={14} aria-hidden="true"/>}
      {expandable?<button type="button" className="portal-agent-row-label portal-agent-row-toggle" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}><span className="portal-agent-action-label">{label}</span>{(change?.file || display.detail) && <span className="portal-agent-target"> {change?.file || display.detail}</span>}<ActivityChangeCounts change={change}/><ChevronDown size={12} aria-hidden="true"/></button>:<span className="portal-agent-row-label">{label}</span>}
      {type!=='text' && statusLabel && <span className="portal-agent-row-meta" title={statusLabel}>
        {state==='running'?<span className="portal-agent-dot" aria-label={statusLabel}/>:state==='completed'?<><Check size={12} aria-label={statusLabel}/>{seconds!==null && <span>{activityDuration(seconds)}</span>}</>:<span>{statusLabel}</span>}
      </span>}
    </div>
    {expanded && (change?<PortalActivityChange change={change}/>:<pre className="portal-agent-detail">{display.detail}</pre>)}
    {!!display?.sources.length && <ul className="portal-agent-sources" aria-label="Sources from this tool">{display.sources.map(source=><li key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer"><PortalSiteIcon href={source.url}/><span>{source.title}</span><small>{new URL(source.url).hostname}</small></a></li>)}</ul>}
    {/* A step the agent reported as blocked is paused in this history. The
        Tasks card above shows the current plan and when it needs the owner. */}
    {!!display?.steps.length && <ol className="portal-agent-plan" aria-label="Reported plan steps">{display.steps.map(step=><li key={step.id} data-state={step.status==='blocked'?'paused':step.status}>{step.status==='completed'?<Check size={13} aria-label="Completed"/>:step.status==='running'?<span className="portal-agent-dot" aria-label="In progress"/>:step.status==='blocked'?<PauseCircle size={13} aria-label="Paused"/>:<Circle size={12} aria-label="Pending"/>}<span>{step.title}</span></li>)}</ol>}
  </li>
}

export default function PortalAgentActivity({task:raw,active=false,status}) {
  const task=parseTaskActivity(raw,raw?.runId)
  const id=useId(), viewport=useRef(null), content=useRef(null), follow=useRef(true), mountedAt=useRef(Date.now())
  // Only the response outcome can need the owner. A receipt that recorded a
  // failed run beside a delivered answer is telemetry, and tool calls that did
  // not work never decide it.
  const ended=status==='error'
  const stopped=status==='stopped'
  const [open,setOpen]=useState(active||ended), [now,setNow]=useState(Date.now()), [fade,setFade]=useState({top:false,bottom:false})
  // Receipts without events group calls by kind. A kind is a tried step only
  // when none of its calls worked.
  const events=task?.events || task?.activities.map((row,i)=>({sequence:i+1,kind:row.kind,state:row.failures===row.calls?'failed':active?'running':'completed',startedAt:task.startedAt,finishedAt:null})) || []
  const lastSequence=events.at(-1)?.sequence
  const progressRows=activityProgressRows(events)
  const start=task?.startedAt?Date.parse(task.startedAt):mountedAt.current
  // A saved running receipt has no confirmed end, even when its response is
  // no longer active. Reload time cannot establish a completed duration.
  const end=task?.finishedAt?Date.parse(task.finishedAt):active?now:null
  const seconds=end===null?null:(end-start)/1000
  const current=[...events].reverse().find(event=>event.state==='running')
  const currentType=current?.display?.type
  const progress=[...events].reverse().find(event=>event.display?.type==='text' && event.state==='completed')?.display?.label
  const activeLabel=progress || (currentType==='search' || current?.kind==='browser'?'Searching the web…':currentType==='text' || !current?'Thinking…':currentType==='steps'?'Planning…':'Working…')
  const lastChange=Math.max(start,...events.map(event=>Date.parse(event.finishedAt || event.startedAt) || 0))
  const quiet=active && !current && now-lastChange>=QUIET_HINT_MS
  const label=active?activeLabel:stopped?'Stopped':ended?'Ended early · you can try again':seconds===null?'Activity recorded':`Worked for ${activityDuration(seconds)}`
  const refreshFade=()=>{const el=viewport.current;if(el)setFade({top:el.scrollTop>2,bottom:el.scrollTop+el.clientHeight<el.scrollHeight-2})}
  useEffect(()=>{
    if(!active)return
    const timer=setInterval(()=>setNow(Date.now()),1000)
    return ()=>clearInterval(timer)
  },[active])
  useEffect(()=>{setOpen(active||ended||stopped);follow.current=true},[active,ended,stopped,task?.runId])
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
    {quiet && <p className="portal-agent-hint" role="status">{QUIET_HINT}</p>}
    <div id={`${id}-log`} role="region" aria-labelledby={`${id}-trigger`} hidden={!open}>
      <div ref={viewport} className="portal-agent-viewport" tabIndex={events.length?0:undefined} aria-label="Activity history" data-fade-top={fade.top} data-fade-bottom={fade.bottom} onScroll={()=>{const el=viewport.current;follow.current=el.scrollTop+el.clientHeight>=el.scrollHeight-12;refreshFade()}}>
        <ol ref={content} className="portal-agent-stream" aria-label="Execution steps">{progressRows.map(row=>row.event?<ActivityRow key={row.event.sequence} event={row.event} active={active} later={row.event.sequence<lastSequence}/>:<li key={row.id} className="portal-agent-progress"><p>{row.text}</p></li>)}</ol>
        {!events.length && !active && <p className="portal-agent-empty">No tool activity was recorded for this response.</p>}
        {task?.calls>events.length && <p className="portal-agent-empty">Showing the latest {events.length} of {task.calls} recorded actions.</p>}
      </div>
      {active && !follow.current && fade.bottom && <button type="button" className="portal-agent-follow" onClick={()=>{follow.current=true;if(viewport.current)viewport.current.scrollTop=viewport.current.scrollHeight;refreshFade()}}>Follow latest</button>}
    </div>
  </section>
}
