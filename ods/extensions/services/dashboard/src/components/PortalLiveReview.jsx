import {useEffect,useState} from 'react'
import PixelFileChanges from './PixelFileChanges'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import {activityChangeRows} from './PortalActivityChange'
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
  const fileEntries=entries.filter(entry=>entry.change.file===current.change.file)
  const latest=new Map(entries.map(entry=>[entry.change.file,entry]))
  latest.set(current.change.file,current)
  // Tool receipts contain excerpts, not verified file coordinates or a complete
  // source snapshot. Share the Review renderer without inventing line numbers.
  const changes=[...latest.values()].map(({change})=>{
    const rows=activityChangeRows(change)
    const bounded=change.truncated || change.before.split('\n').length>200 || change.after.split('\n').length>200
    const counts=!bounded && change.kind!=='write'
    return {path:change.file,change:'modified',truncated:bounded,
      additions:counts?rows.filter(row=>row.type==='add').length:null,
      deletions:counts?rows.filter(row=>row.type==='remove').length:null,
      diff:rows.map(row=>({...row,oldLine:null,newLine:null}))}
  })
  return <section className="portal-live-review portal-live-review-files" data-published={hasPublication} aria-label="Recent file changes">
    <PixelFileChanges changes={changes} excerpt selectedPath={current.change.file}
      onSelectFile={path=>setSelected(entries.findLast(entry=>entry.change.file===path).id)}
      headerAccessory={fileEntries.length>1 && <select aria-label="Edit revision" value={current.id} onChange={event=>setSelected(event.target.value)}>{fileEntries.map((entry,index)=><option key={entry.id} value={entry.id}>Edit {index+1}</option>)}</select>}/>
  </section>
}
