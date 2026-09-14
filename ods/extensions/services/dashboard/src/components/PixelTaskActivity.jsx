import { useState } from 'react'
import { BookOpen, Bot, Terminal, Pencil, Globe, PanelRight, ArrowRight, Wrench, Activity } from 'lucide-react'
import { parseTaskActivity } from '../lib/pixelTaskActivity'

const kinds = {
  read: ['Read', BookOpen, 'Read-only context or source access'],
  agent: ['Agent', Bot, 'Agent or subtask coordination'],
  run: ['Run', Terminal, 'Local commands, checks, or validation'],
  edit: ['Edit', Pencil, 'Local edit operations'],
  browser: ['Browser', Globe, 'Browser or web-source operations'],
  preview: ['Preview', PanelRight, 'Workspace preview publication'],
  action: ['Action', ArrowRight, 'Provider or operations actions'],
  unknown: ['Tool', Wrench, 'Other tool operations'],
}

export default function PixelTaskActivity({messages, sending, elapsed}) {
  const [selected, setSelected] = useState('latest')
  let prompt = ''
  const turns = []
  messages.forEach((message, index) => {
    if (message.role === 'user') prompt = message.content
    else if (message.role === 'assistant') turns.push({index, prompt, task:parseTaskActivity(message.task, message.task?.runId)})
  })
  const turn = selected === 'latest' ? turns.at(-1) : turns.find(item => String(item.index) === selected)
  const task = turn?.task
  const active = sending && selected === 'latest'
  const duration = task?.finishedAt ? Math.max(0, Math.round((Date.parse(task.finishedAt) - Date.parse(task.startedAt)) / 1000)) : null
  return <section className="pixel-task-activity" aria-label="Workspace activity">
    <div className="pixel-activity-selector">
      <Activity size={15}/>
      <select aria-label="Activity turn" value={selected} onChange={event => setSelected(event.target.value)}>
        <option value="latest">Latest turn</option>
        {turns.slice(0,-1).reverse().map(item => <option key={item.index} value={String(item.index)}>{item.prompt.slice(0,80) || 'Earlier turn'}</option>)}
      </select>
    </div>
    <header>
      <h2>{turn?.prompt || 'Ready for your next task'}</h2>
      <p role="status">{active ? `Working · ${elapsed} elapsed` : task ? `${task.state === 'failed' ? 'Turn ended with an error' : task.state === 'running' ? 'Completion not observed' : 'Turn ended'}${duration !== null ? ` · ${duration}s` : ''}` : 'No recorded tool activity for this turn'}</p>
    </header>
    {task ? <>
      <div className="pixel-activity-heading"><h3>Tools</h3><span>{task.calls} calls</span></div>
      <ul>
        {task.activities.map(item => {
          const [label, Icon, detail] = kinds[item.kind]
          return <li key={item.kind}>
            <Icon size={16}/>
            <div><strong>{label}</strong><small>{detail}</small>
              {item.failures > 0 && <small className="pixel-activity-warning">{item.failures} failed{item.blocked ? ` · ${item.blocked} blocked before execution` : ''}</small>}
            </div>
            <span>{item.calls}</span>
          </li>
        })}
      </ul>
      {task.calls === 0 && <p className="pixel-activity-note">{active ? 'The runtime started this turn. No tool calls observed yet.' : 'No tool calls were observed. This turn used the model only.'}</p>}
      {task.truncated && <p className="pixel-activity-warning">Only the first 512 calls are included.</p>}
      <p className="pixel-activity-note">Recorded by the assistant runtime. Tool calls show attempts, not proof that the requested result works. Saved with this conversation in this browser.</p>
    </> : <p className="pixel-activity-note">{active ? 'Waiting for runtime observations. This runtime may not provide live activity.' : 'Older replies do not contain runtime observations. They are not reconstructed from the assistant’s claims.'}</p>}
  </section>
}
