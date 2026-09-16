import {parseTaskActivity} from '../lib/pixelTaskActivity'

const labels = {read:'Reading', run:'Commands / checks', edit:'File edits', browser:'Web / browser', preview:'Preview publication', action:'Operations', agent:'Agent coordination', unknown:'Other tools'}

export default function PixelLiveActivity({task: raw, active = false}) {
  const task = parseTaskActivity(raw, raw?.runId)
  if (!task) return null
  return <details className="pixel-live-activity">
    <summary>{active ? 'Live activity' : 'Recorded activity'} · {task.calls} tool {task.calls === 1 ? 'call' : 'calls'}</summary>
    <ul>{task.activities.map(item => <li key={item.kind}><span>{labels[item.kind]}</span><span>{item.calls}{item.failures ? ` · ${item.failures} failed` : ''}</span></li>)}</ul>
    {task.calls === 0 && <p>{active ? 'The runtime started this turn. No tool calls observed yet.' : 'No tool calls were recorded.'}</p>}
    <p>Runtime observations, not private reasoning or proof of completion.{task.truncated ? ' Limited to the first 512 calls.' : ''}</p>
  </details>
}
