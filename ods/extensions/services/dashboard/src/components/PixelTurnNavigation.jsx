export default function PixelTurnNavigation({messages, onNavigate}) {
  const turns = messages.flatMap((message,index) => message.role === 'user' ? [{index,text:message.content}] : [])
  if (turns.length < 2) return null
  return <div className="p-2 text-xs">
    <label>Conversation outline
      <select aria-label="Jump to conversation turn" value="" className="mt-1 block w-full min-w-0 rounded border border-theme-border bg-theme-bg p-2" onChange={event => {
        const selected = turns.find(turn => String(turn.index) === event.target.value)
        if (selected) {onNavigate(selected.index); event.currentTarget.closest('details')?.removeAttribute('open')}
      }}>
        <option value="" disabled>Jump to a prompt…</option>
        {turns.map((turn,index) => <option key={turn.index} value={turn.index}>Turn {index+1} · {turn.text.trim().slice(0,80) || 'Empty prompt'}</option>)}
      </select>
    </label>
    <button type="button" onClick={event => {onNavigate(messages.length-1); event.currentTarget.closest('details')?.removeAttribute('open')}}>Go to latest message</button>
  </div>
}
