import {useId, useState} from 'react'
import {CheckCircle2, Circle, CircleDot, ListChecks, AlertCircle, PauseCircle, ChevronDown} from 'lucide-react'
import {parseTaskActivity} from '../lib/pixelTaskActivity'
import './portal-task-list.css'

const stepLabels = {pending:'Pending',running:'In progress',completed:'Completed',blocked:'Blocked',paused:'Paused',waiting:'Waiting for you'}

function PlanTasks({plan, active, onResume, disabled}) {
  const [open,setOpen] = useState(true)
  const id = useId()
  const completed = plan.steps.filter(step => step.status === 'completed').length
  const total = plan.steps.length
  const running = active && plan.status === 'active'
  const status = plan.status === 'active' && !active ? 'Paused' : {active:'Working toward your goal',completed:'Plan completed',blocked:'Needs attention',waiting:'Waiting for you'}[plan.status]
  const progress = total ? completed / total : 0

  return <section className="portal-task-plan" aria-label="Goal plan" data-status={running ? 'running' : plan.status}>
    <button type="button" className="portal-task-list-toggle" id={`${id}-trigger`} aria-label="Toggle tasks" aria-expanded={open} aria-controls={`${id}-tasks`} onClick={() => setOpen(value => !value)}>
      <span className="portal-task-list-symbol" aria-hidden="true">
        {plan.status === 'completed' ? <CheckCircle2 size={17}/> : total ? <svg className="portal-task-list-ring" width="17" height="17" viewBox="0 0 20 20"><circle className="portal-task-list-track" cx="10" cy="10" r="7.5"/><circle className="portal-task-list-progress" cx="10" cy="10" r="7.5" pathLength="100" strokeDasharray={`${progress * 100} 100`}/></svg> : <ListChecks size={16}/>}
      </span>
      <span className="portal-task-list-title">Tasks</span>
      <span className="portal-task-list-count" role="status" aria-label={total ? `${completed} of ${total} tasks reported completed` : 'Planning'}>{total ? `${completed}/${total}` : 'Planning'}</span>
      <ChevronDown className="portal-task-list-chevron" size={13} aria-hidden="true"/>
    </button>
    <div className="portal-task-list-description"><p>{plan.summary}</p>{!running && <span className="portal-task-list-state">{status}</span>}</div>
    <div id={`${id}-tasks`} role="region" aria-labelledby={`${id}-trigger`} hidden={!open}>
      <ol className="portal-task-list" aria-label="Reported tasks">{plan.steps.map(step => {
        const state = step.status === 'running' && !running ? plan.status === 'waiting' ? 'waiting' : 'paused' : step.status
        const Icon = state === 'completed' ? CheckCircle2 : state === 'running' ? CircleDot : state === 'blocked' ? AlertCircle : ['paused','waiting'].includes(state) ? PauseCircle : Circle
        return <li key={step.id} data-state={state}>
          <Icon className="portal-task-list-icon" size={16} aria-hidden="true"/>
          <span className="portal-task-list-label">{step.title}</span>
          <span className="portal-task-list-item-state">Status: {stepLabels[state]}</span>
        </li>
      })}</ol>
      {!total && <p className="portal-task-list-empty">Waiting for a plan.</p>}
    </div>
    <footer className="portal-task-list-footer"><small>Progress reported by the agent.</small>
      {!active && onResume && plan.status !== 'completed' && <button type="button" disabled={disabled} onClick={onResume}>Continue goal</button>}
    </footer>
  </section>
}

// Inspired by https://www.aicss.dev/r/task-list.json. Only a validated public
// plan drives these states; no timer advances or completes work for the agent.
export default function PortalGoalPlan({task:raw, active=false, onResume, disabled=false}) {
  const task = parseTaskActivity(raw,raw?.runId)
  if (!task) return null
  let plan = task.goal
  if (!plan) {
    const reported = [...(task.events || [])].reverse().find(event => event.state === 'completed' && event.display?.type === 'steps' && event.display.steps.length)?.display
    if (!reported) return null
    plan = {summary:reported.label,steps:reported.steps,status:reported.steps.every(step => step.status === 'completed') ? 'completed' : reported.steps.some(step => step.status === 'blocked') ? 'blocked' : 'active'}
  }
  return <PlanTasks key={task.runId} plan={plan} active={active} onResume={task.goal ? onResume : undefined} disabled={disabled}/>
}
