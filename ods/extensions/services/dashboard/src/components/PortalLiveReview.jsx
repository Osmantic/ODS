import {useEffect,useState} from 'react'
import {FilePenLine} from 'lucide-react'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import PortalActivityChange from './PortalActivityChange'
import './portal-agent-activity.css'

// Consume the same validated, redacted public events as the conversation.
// An attempted/failed write is never presented as a completed file change.
export function completedReviewChanges(raw) {
  const task=parseTaskActivity(raw,raw?.runId)
  return task?.events?.filter(event=>event.state==='completed' && event.display?.change)
    .map(event=>({id:`${task.runId}/${event.sequence}`,change:event.display.change})) || []
}

export default function PortalLiveReview({task,hasPublication=false}) {
  const [saved,setSaved]=useState({runId:null,entries:[]})
  const [selected,setSelected]=useState(null)
  const incoming=completedReviewChanges(task)
  const entries=saved.runId===task?.runId?saved.entries:[]
  useEffect(()=>{
    const updates=completedReviewChanges(task)
    setSaved(previous=>{
      const entries=previous.runId===task?.runId?previous.entries:[]
      const merged=new Map(entries.map(entry=>[entry.id,entry]))
      for(const entry of updates)merged.set(entry.id,entry)
      return {runId:task?.runId,entries:[...merged.values()].slice(-128)}
    })
    setSelected(null)
  },[task?.runId])
  useEffect(()=>{
    if(!incoming.length)return
    setSaved(previous=>{
      const entries=previous.runId===task?.runId?previous.entries:[]
      const merged=new Map(entries.map(entry=>[entry.id,entry]))
      let changed=false
      for(const entry of incoming)if(!merged.has(entry.id)){merged.set(entry.id,entry);changed=true}
      return changed?{runId:task?.runId,entries:[...merged.values()].slice(-128)}:previous
    })
  },[task])
  const current=entries.find(entry=>entry.id===selected) || entries.at(-1)
  if(!current)return null
  return <section className="portal-live-review" data-published={hasPublication} aria-label="Recent file changes">
      <nav aria-label="Completed file edits">{entries.map((entry,index)=><button key={entry.id} type="button" aria-pressed={entry.id===current.id} onClick={()=>setSelected(entry.id)}><FilePenLine size={13}/><span>{entry.change.file}</span><small>{index+1}</small></button>)}</nav>
      <div className="portal-live-review-content"><PortalActivityChange key={current.id} change={current.change}/></div>
    <small className="portal-live-review-note">Tool-reported changes · excerpts may be shortened. The website preview updates after publication.</small>
  </section>
}
