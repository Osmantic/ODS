import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ChevronRight, Circle, AlertCircle, Check, RotateCcw, Square, Users} from 'lucide-react'
import {ACTIVE_TEAMS} from '../lib/portalTeams'
import {parseQuestions} from '../lib/pixelQuestions'
import {MiniPortal} from './PortalAgentDock'
import PixelQuestions from './PixelQuestions'
import PortalAgentActivity from './PortalAgentActivity'
import PortalGoalPlan from './PortalGoalPlan'
import PortalStreamingText from './PortalStreamingText'
import PortalResponseActions from './PortalResponseActions'
import './portal-subagents.css'

const states = {queued:'Queued',running:'Working',waiting:'Your input',completed:'Completed',failed:'Needs attention',cancelled:'Stopped',skipped:'Skipped',interrupted:'Unconfirmed',stopping:'Stopping'}
const activeStates = new Set(['queued','running','waiting','stopping'])
const groups = [{id:'active',label:'Active',Icon:Circle},{id:'attention',label:'Needs attention',Icon:AlertCircle},{id:'completed',label:'Completed',Icon:Check}]
const text = value => typeof value === 'string' ? value : ''
// Do not load remote images automatically from an agent's Markdown transcript.
const markdownComponents = {img:({alt}) => <span className="portal-subagents-image">{alt || 'Image'}</span>}

function restoreDraft(key, questions) {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(questions.flatMap(question => typeof value[question.id] === 'string' && value[question.id].length <= 1000 ? [[question.id,value[question.id]]] : []))
  } catch { return {} }
}

function AgentQuestions({team,agent,questions,acting,perform,answer}) {
  const key = `portal-team-answer:${team.id}:${agent.id}:${agent.turn}`
  const [draft,setDraft] = useState(() => restoreDraft(key,questions))
  function changeDraft(value) {
    setDraft(value)
    try { sessionStorage.setItem(key,JSON.stringify(value)) } catch { /* The current answer remains usable without storage. */ }
  }
  return <PixelQuestions questions={questions} answers={draft} onChange={changeDraft} disabled={acting || !answer}
    onSubmit={() => perform(() => answer(team.id,agent.id,draft))}/>
}

function AgentList({teams,select,error}) {
  const rows = teams.flatMap(team => (team.agents || []).map((agent,index) => ({team,agent,index,group:agent.status === 'completed' ? 'completed' : activeStates.has(agent.status) ? 'active' : 'attention'})))
  return <div className="portal-subagents-list">
    <header className="portal-subagents-list-heading"><h2>Subagents</h2><span>{rows.length}</span></header>
    {error && <p className="portal-subagents-notice" role="alert">{error}</p>}
    {!rows.length && <div className="portal-subagents-empty"><Users size={22} aria-hidden="true"/><p>Subagents will appear here when a team starts.</p></div>}
    {groups.map(({id,label,Icon}) => {
      const entries = rows.filter(row => row.group === id)
      if (!entries.length) return null
      return <section className="portal-subagents-group" key={id} aria-label={label}>
        <h3><Icon size={13} aria-hidden="true"/>{label}<span>{entries.length}</span></h3>
        <ul>{entries.map(({team,agent,index}) => <li key={`${team.id}:${agent.id}`}>
          <button type="button" className="portal-subagents-item" data-agent-key={`${team.id}:${agent.id}`} onClick={() => select({teamId:team.id,agentId:agent.id})} aria-label={`${agent.name} · ${states[agent.status] || agent.status}`}>
            <MiniPortal index={index} state={agent.status}/>
            <span className="portal-subagents-item-body"><span className="portal-subagents-item-title"><strong>{agent.name}</strong><span data-state={agent.status}>{states[agent.status] || agent.status}</span></span><span className="portal-subagents-item-goal">{text(team.goal) || text(agent.task)}</span></span>
            <ChevronRight size={14} aria-hidden="true"/>
          </button>
        </li>)}</ul>
      </section>
    })}
  </div>
}

function AgentConversation({team,agent,index,controller,acting,perform,localError,renderApproval}) {
  const transcript = (Array.isArray(agent.conversation) ? agent.conversation : []).filter(message => ['user','assistant'].includes(message?.role) && typeof message.content === 'string' && message.content.trim())
  const last = transcript.at(-1)
  const output = text(agent.output)
  const duplicateOutput = last?.role === 'assistant' && last.content === output
  const active = agent.status === 'running'
  const questions = agent.status === 'waiting' ? parseQuestions(agent.questions) : null
  const error = localError || text(controller.error) || text(agent.error) || text(team.notice)
  const scroll = useRef(null), follow = useRef(true)
  const [showLatest,setShowLatest] = useState(false)
  const complete = (team.agents || []).filter(item => item.status === 'completed').length
  function followOutput() {
    const element = scroll.current
    if (element && follow.current) element.scrollTop = element.scrollHeight
  }
  useEffect(() => {
    followOutput()
    if (typeof ResizeObserver === 'undefined' || !scroll.current?.firstElementChild) return
    const observer = new ResizeObserver(followOutput)
    observer.observe(scroll.current.firstElementChild)
    return () => observer.disconnect()
  },[])
  useEffect(followOutput,[agent.output,agent.conversation,agent.activity,agent.questions,agent.status,error])

  return <>
    <header className="portal-subagents-detail-heading">
      <button type="button" className="portal-subagents-back" aria-label="Back to subagents" onClick={() => controller.select({teamId:team.id,agentId:null})}><ArrowLeft size={17}/></button>
      <MiniPortal index={index} state={agent.status}/>
      <div><h2>{agent.name}</h2><span data-state={agent.status}>{states[agent.status] || agent.status}</span></div>
      <span className="portal-subagents-team-progress" title="Completed agents in this team">{complete}/{team.agents.length}</span>
    </header>
    <div ref={scroll} className="portal-subagents-conversation" role="region" aria-label={`${agent.name} conversation`} tabIndex={0} onScroll={() => {
      const element = scroll.current
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48
      setShowLatest(!follow.current)
    }}>
      <div className="portal-subagents-messages">
        {!transcript.length && (team.goal || agent.task) && <div className="portal-subagents-assignment"><span>Assignment</span><p>{text(team.goal) || text(agent.task)}</p>{team.goal && agent.task && <details><summary>Agent role</summary><p>{agent.task}</p></details>}</div>}
        {transcript.map((message,messageIndex) => <article key={messageIndex} className={`portal-subagents-message is-${message.role}`} aria-label={message.role === 'user' ? 'Assignment or reply' : `${agent.name} response`}>
          <PortalStreamingText instant components={markdownComponents}>{message.content}</PortalStreamingText>
          {message.role === 'assistant' && <><PortalResponseActions content={message.content}/>{renderApproval?.(message.content)}</>}
        </article>)}
        {agent.status === 'queued' && <p className="portal-subagents-status">Waiting for its turn; it has not started.</p>}
        {agent.runtime_wait && <p className="portal-subagents-status">Waiting for the model runtime to become ready. No new work has been sent.</p>}
        <PortalGoalPlan task={agent.activity} active={active && !agent.runtime_wait}/>
        <PortalAgentActivity key={agent.activity?.runId || `${agent.id}:${agent.turn}`} task={agent.activity} active={active && !agent.runtime_wait} status={agent.status === 'failed' ? 'error' : ['cancelled','skipped','interrupted'].includes(agent.status) ? 'stopped' : undefined}/>
        {output.trim() && !duplicateOutput && <article className="portal-subagents-message is-assistant" aria-label={`${agent.name} live response`}><PortalStreamingText active={active} onReveal={followOutput} components={markdownComponents}>{output}</PortalStreamingText>{!active && <PortalResponseActions content={output}/>}</article>}
        {questions && <AgentQuestions key={`${team.id}:${agent.id}:${agent.turn}`} team={team} agent={agent} questions={questions} acting={acting} perform={perform} answer={controller.answer}/>}
        {error && <p className="portal-subagents-notice" role="alert"><AlertCircle size={14} aria-hidden="true"/>{error}</p>}
        {!transcript.length && !output.trim() && agent.status === 'completed' && <p className="portal-subagents-status">This agent finished without a recorded response.</p>}
      </div>
    </div>
    {showLatest && <button type="button" className="portal-subagents-latest" onClick={() => {follow.current=true;followOutput();setShowLatest(false)}}>Jump to latest</button>}
    {(agent.retryable && controller.retry || ACTIVE_TEAMS.has(team.status) && controller.stop) && <footer className="portal-subagents-actions">
      {agent.retryable && controller.retry && <button type="button" disabled={acting} onClick={() => perform(() => controller.retry(team.id,agent.id))}><RotateCcw size={14} aria-hidden="true"/>Retry agent</button>}
      {ACTIVE_TEAMS.has(team.status) && controller.stop && <button type="button" disabled={acting} onClick={() => perform(() => controller.stop(team.id))}><Square size={12} aria-hidden="true"/>{team.status === 'stopping' ? 'Confirm stop' : 'Stop team'}</button>}
    </footer>}
  </>
}

export default function PortalSubagents({controller,renderApproval}) {
  const teams = Array.isArray(controller?.teams) ? controller.teams : []
  const selected = controller?.selected
  const team = teams.find(item => item.id === selected?.teamId)
  const index = team?.agents?.findIndex(item => item.id === selected?.agentId) ?? -1
  const agent = index >= 0 ? team.agents[index] : null
  const selectionKey = agent ? `${team.id}:${agent.id}` : ''
  const [acting,setActing] = useState(false), [failure,setFailure] = useState(null)
  const busy = useRef(false), mounted = useRef(true)
  const root = useRef(null), focusedSelection = useRef('')
  useEffect(() => {mounted.current=true;return () => {mounted.current=false}},[])
  // Visibility can change after selection. Defer focus until this tab is shown,
  // and do not move it again for polling or ordinary workspace tab switches.
  useEffect(() => {
    if (focusedSelection.current === selectionKey || !root.current || root.current.closest('[hidden]')) return
    const previous = focusedSelection.current
    focusedSelection.current = selectionKey
    const target = agent ? root.current.querySelector('.portal-subagents-back')
      : [...root.current.querySelectorAll('[data-agent-key]')].find(item => item.dataset.agentKey === previous)
    target?.focus()
  })
  async function perform(action) {
    if (busy.current) return
    busy.current=true
    setActing(true)
    setFailure(null)
    try { await action() }
    catch (error) { if (mounted.current) setFailure({key:selectionKey,message:error?.message || 'The action could not be completed. Try again.'}) }
    finally {busy.current=false;if (mounted.current) setActing(false)}
  }
  return <section ref={root} className="portal-subagents" aria-label="Subagents workspace">
    {agent ? <AgentConversation key={selectionKey} team={team} agent={agent} index={index} controller={controller} acting={acting} perform={perform} localError={failure?.key === selectionKey ? failure.message : ''} renderApproval={renderApproval}/> : <AgentList teams={teams} select={controller?.select} error={text(controller?.error)}/>}
  </section>
}
