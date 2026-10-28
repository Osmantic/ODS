import {useEffect,useRef,useState} from 'react'
import {ArrowLeft,Check,Clock3,Pause,Play,Plus,RefreshCw,Trash2} from 'lucide-react'
import {requestSchedules,scheduleError,scheduleLabel,newScheduleRequestId} from '../lib/portalSchedules'
import PortalStreamingText from './PortalStreamingText'
import './portal-scheduled-tasks.css'

const stamp=value=>value===null?'Not yet':new Date(value).toLocaleString()
const status={ok:'Completed',error:'Needs attention',skipped:'Skipped',unknown:'Unconfirmed'}
const emptyForm=()=>({name:'',task:'',frequency:'daily',time:'09:00',at:'',weekday:'1'})
const timezone=()=>Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
// Saved summaries use Portal's Markdown/link policy without loading model-supplied images.
const markdownComponents={img:({alt})=><span className="portal-schedules-image">{alt || 'Image'}</span>}
// transition-busy is the Root lock refusal, before any owner task or receipt.
const refusedCreate=error=>new Set(['invalid-task','task-too-large','task-time-passed','task-time-too-far',
  'invalid-task-timezone','task-history-full','task-config-changed','runtime-change-pending',
  'schedule-installation-mismatch','transition-busy']).has(error?.code?.replace(/^controller-/,''))

export default function PortalScheduledTasks() {
  const [view,setView]=useState('list'),[offset,setOffset]=useState(0),[page,setPage]=useState(null)
  const [selected,setSelected]=useState(null),[history,setHistory]=useState(null),[runOffset,setRunOffset]=useState(0)
  const [form,setForm]=useState(emptyForm),[creating,setCreating]=useState(false),[zone]=useState(timezone)
  const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState('')
  const [unknown,setUnknown]=useState(null),[confirmRemove,setConfirmRemove]=useState(false)
  const current=useRef(null),mounted=useRef(false),nameInput=useRef(null)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;current.current?.abort.abort()}},[])
  async function operate(body,onSuccess) {
    current.current?.abort.abort()
    const request={abort:new AbortController()}
    current.current=request
    const timer=setTimeout(()=>request.abort.abort(),210000)
    setBusy(true);setError('');setNotice('')
    try {
      const result=await requestSchedules(body,request.abort.signal)
      if (mounted.current && current.current===request) onSuccess(result)
    } catch (cause) {
      if (mounted.current && current.current===request) {
        setError(scheduleError(cause,body.action))
        if (body.action==='create' && !refusedCreate(cause)) setUnknown({requestId:body.requestId,name:body.name})
      }
    } finally {
      clearTimeout(timer)
      if (mounted.current && current.current===request) {current.current=null;setBusy(false)}
    }
  }
  function load(kind=view,start=offset) {
    setPage(null);setSelected(null);setHistory(null);setConfirmRemove(false)
    operate({action:kind,offset:start},setPage)
  }
  useEffect(()=>{load(view,offset)},[view,offset])
  useEffect(()=>{if(creating)nameInput.current?.focus()},[creating])
  function open(task) {
    setSelected(task);setRunOffset(0);setHistory(null);setConfirmRemove(false)
    operate({action:'runs',id:task.id,offset:0},setHistory)
  }
  function runs(start) {
    setRunOffset(start);setHistory(null)
    operate({action:'runs',id:selected.id,offset:start},setHistory)
  }
  function changed(result,message) {
    if (result.outcome==='unconfirmed') {setNotice('This change is not confirmed. Refresh and check the task before trying another change.');return}
    setConfirmRemove(false);setSelected(null);setHistory(null);setPage(null)
    // Refresh is an explicit read after the acknowledged write, never a write retry.
    load(view,offset);setNotice(message)
  }
  function create(event) {
    event.preventDefault()
    if (busy || unknown) return
    if (!form.name.trim() || !form.task.trim() || new TextEncoder().encode(form.name).length>160
        || new TextEncoder().encode(form.task).length>4000) {setError('Use a shorter name or instructions before scheduling.');return}
    const instant=new Date(form.at)
    if (form.frequency==='once' && (!Number.isFinite(instant.getTime()) || instant.getTime()<=Date.now())) {
      setError('Choose a date and time in the future.');return
    }
    const schedule=form.frequency==='once'?{kind:'at',at:instant.toISOString()}
      :{kind:'recurring',frequency:form.frequency,time:form.time,timeZone:zone,weekday:form.frequency==='weekly'?Number(form.weekday):null}
    let requestId
    try {requestId=newScheduleRequestId()} catch {setError('This browser could not create a request identity. Try a current browser.');return}
    const body={action:'create',requestId,name:form.name,task:form.task,schedule}
    operate(body,result=>{
      if(result.outcome==='unconfirmed') {
        setUnknown({requestId:body.requestId,name:body.name})
        setNotice('The request was sent, but creation is not confirmed. Check it before creating another task.')
      } else {
        setCreating(false);setForm(emptyForm());setUnknown(null)
        changed(result,'Task scheduled.')
      }
    })
  }
  function check(pending) {
    operate({action:'check-create',requestId:pending.requestId},result=>{
      if(result.outcome==='unconfirmed') {setNotice('This request is still unconfirmed. It has not been sent again.');return}
      setUnknown(null);setCreating(false);setForm(emptyForm())
      changed(result,'The existing task was found. No duplicate was created.')
    })
  }
  function dismiss(pending) {
    operate({action:'dismiss-create',requestId:pending.requestId},result=>{
      setUnknown(null);setCreating(false);setForm(emptyForm())
      changed(result,'Notice dismissed. This does not stop an existing task; check Current tasks.')
    })
  }
  const hasEnded=selected?.schedule.kind==='at' && Date.parse(selected.schedule.at)<=Date.now()
  const pending=[...(unknown?[unknown]:[]),...(page?.pendingCreates || []).filter(item=>item.requestId!==unknown?.requestId)]
  return <section className="portal-schedules" aria-label="Scheduled tasks">
    <header className="portal-schedules-heading"><div><h2>Scheduled tasks</h2><p>Let Portal work on a schedule.</p></div>
      <button type="button" disabled={busy} aria-label="Refresh scheduled tasks" onClick={()=>selected?runs(runOffset):load()}><RefreshCw size={14}/></button>
    </header>
    <p className="portal-schedules-help">ODS must stay running. You can close the browser and return here for results. Tasks use Portal’s model and permissions when they run.</p>
    {error && <p role="alert" className="portal-schedules-notice">{error}</p>}
    {notice && <p role="status" className="portal-schedules-notice">{notice}</p>}
    {busy && <p role="status" className="portal-schedules-loading">Checking ODS…</p>}
    {pending.map(item=><aside key={item.requestId} className="portal-schedules-pending"><strong>{item.name}</strong><p>Creation is unconfirmed. Checking will not send it again.</p><div>
      <button type="button" disabled={busy} onClick={()=>check(item)}>Check request</button>
      <button type="button" disabled={busy} onClick={()=>dismiss(item)}>Dismiss notice</button></div><small>Dismissing does not cancel a task that may already exist.</small>
    </aside>)}
    {page && page.pendingCount>page.pendingCreates.length && <p className="portal-schedules-help">More requests are unconfirmed. Check these first, then refresh.</p>}
    {page?.pendingRemovals.map(item=><aside key={item.id} className="portal-schedules-pending"><strong>{item.name}</strong><p>Removal is unconfirmed. A run already underway may finish.</p><button type="button" disabled={busy} onClick={()=>operate({action:'remove',id:item.id,revision:item.revision},result=>changed(result,'Task removed. Its results are kept in Past tasks.'))}>Check removal</button></aside>)}
    {selected ? <div className="portal-schedules-detail">
      <button type="button" className="portal-schedules-back" disabled={busy} onClick={()=>{setSelected(null);setHistory(null);setConfirmRemove(false)}}><ArrowLeft size={14}/>Back to tasks</button>
      <h3>{selected.name}</h3><p className="portal-schedules-time">{scheduleLabel(selected.schedule)}</p>
      <p className="portal-schedules-instructions">{selected.task}</p>
      {selected.externalDelivery && <p className="portal-schedules-help">This existing task also has delivery configured outside Portal.</p>}
      {!selected.removed && <div className="portal-schedules-actions">
        {(!hasEnded || selected.enabled) && <button type="button" disabled={busy || selected.removalPending} onClick={()=>operate({action:'set-enabled',id:selected.id,revision:selected.revision,enabled:!selected.enabled},result=>changed(result,selected.enabled?'Future runs paused.':'Task resumed.'))}>{selected.enabled?<Pause size={13}/>:<Play size={13}/>} {selected.enabled?'Pause':'Resume'}</button>}
        <button type="button" disabled={busy} onClick={()=>setConfirmRemove(true)}><Trash2 size={13}/>Remove schedule</button>
      </div>}
      {confirmRemove && <div className="portal-schedules-pending" role="group" aria-label="Confirm schedule removal"><p>Stop future runs of “{selected.name}”? Its saved results will stay in Past tasks. A run already underway may finish.</p><div><button type="button" disabled={busy} onClick={()=>operate({action:'remove',id:selected.id,revision:selected.revision},result=>changed(result,'Task removed. Its results are kept in Past tasks.'))}>Remove schedule</button><button type="button" disabled={busy} onClick={()=>setConfirmRemove(false)}>Keep schedule</button></div></div>}
      <h4>Results</h4>
      {history?.items.length===0 && <p className="portal-schedules-help">No recorded results yet.</p>}
      {history?.items.map(result=><article className="portal-schedules-result" key={result.timestamp}><header><strong>{status[result.status]}</strong><time dateTime={new Date(result.timestamp).toISOString()}>{stamp(result.timestamp)}</time></header><PortalStreamingText instant components={markdownComponents}>{result.summary || 'This run did not leave a summary.'}</PortalStreamingText>{result.summaryTruncated && <small>This summary was shortened for display.</small>}</article>)}
      {history && <nav className="portal-schedules-pages" aria-label="Result pages"><button type="button" disabled={busy || runOffset===0} onClick={()=>runs(Math.max(0,runOffset-1))}>Newer result</button><button type="button" disabled={busy || !history.hasMore} onClick={()=>runs(history.nextOffset)}>Older result</button></nav>}
    </div> : <>
      <nav className="portal-schedules-switch" aria-label="Task lists">{[['list','Current tasks'],['archived','Past tasks']].map(([kind,label])=><button type="button" key={kind} aria-pressed={view===kind} disabled={busy} onClick={()=>{setCreating(false);setView(kind);setOffset(0)}}>{label}</button>)}</nav>
      {view==='list' && !creating && <button type="button" className="portal-schedules-new" disabled={busy || !!unknown} onClick={()=>setCreating(true)}><Plus size={14}/>New scheduled task</button>}
      {creating && <form className="portal-schedules-form" onSubmit={create}>
        <h3>New scheduled task</h3>
        <label>Name<input ref={nameInput} required maxLength={160} value={form.name} disabled={busy || !!unknown} onChange={event=>setForm({...form,name:event.target.value})} placeholder="Daily briefing"/></label>
        <label>What should Portal do?<textarea required maxLength={4000} value={form.task} disabled={busy || !!unknown} onChange={event=>setForm({...form,task:event.target.value})} rows={4} placeholder="Summarize my upcoming day using my connected calendar."/></label>
        <p className="portal-schedules-help">Portal can only use apps you have connected. Actions that need your approval may wait for you.</p>
        <label>Repeat<select value={form.frequency} disabled={busy || !!unknown} onChange={event=>setForm({...form,frequency:event.target.value})}><option value="daily">Daily</option><option value="weekdays">Weekdays</option><option value="weekly">Weekly</option><option value="once">Once</option></select></label>
        {form.frequency==='weekly' && <label>Day<select value={form.weekday} disabled={busy || !!unknown} onChange={event=>setForm({...form,weekday:event.target.value})}>{['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map((day,index)=><option key={day} value={index}>{day}</option>)}</select></label>}
        <label>{form.frequency==='once'?'Date and time':'Time'}<input required type={form.frequency==='once'?'datetime-local':'time'} value={form.frequency==='once'?form.at:form.time} disabled={busy || !!unknown} onChange={event=>setForm({...form,[form.frequency==='once'?'at':'time']:event.target.value})}/></label>
        <p className="portal-schedules-time">Timezone: {zone}</p>
        <div className="portal-schedules-actions"><button type="submit" disabled={busy || !!unknown}><Check size={14}/>Schedule task</button><button type="button" disabled={busy} onClick={()=>setCreating(false)}>Cancel</button></div>
      </form>}
      {page?.items.length===0 && !creating && <div className="portal-schedules-empty"><Clock3 size={24}/><h3>{view==='list'?'No scheduled tasks yet':'No past tasks yet'}</h3><p>{view==='list'?'Schedule a useful check-in or a recurring task.':'Removed schedules appear here with their recorded results.'}</p></div>}
      {page?.items.map(item=><button type="button" key={item.id} className="portal-schedules-row" disabled={busy} onClick={()=>open(item)}><span><strong>{item.name}</strong><span>{scheduleLabel(item.schedule)}</span><small>{item.runningSince!==null?'Running':item.removed?'Removed':item.enabled?'Scheduled':item.schedule.kind==='at' && Date.parse(item.schedule.at)<=Date.now()?'Finished':'Paused'}{item.lastStatus?` · ${status[item.lastStatus]}`:''}</small></span><span className="portal-schedules-next">{item.nextRunAt!==null?`Next: ${stamp(item.nextRunAt)}`:'View results'}</span></button>)}
      {page && <nav className="portal-schedules-pages" aria-label="Task pages"><button type="button" disabled={busy || offset===0} onClick={()=>setOffset(Math.max(0,offset-10))}>Previous</button><button type="button" disabled={busy || !page.hasMore} onClick={()=>setOffset(page.nextOffset)}>Next</button></nav>}
    </>}
  </section>
}
