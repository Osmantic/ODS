import { useState } from 'react'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'
const cardStyle = 'rounded-xl border border-theme-border bg-theme-card p-5 space-y-4'

function Fact({ item, busy, onEdit, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(item.text)
  const save = async () => { if ((await onEdit(item.id, text)).ok) setEditing(false) }
  if (editing) {
    return <li className="space-y-2 rounded border border-theme-border p-3 text-sm">
      <label className="block">Memory<input className={inputStyle} value={text} maxLength={240} disabled={busy} autoFocus
        onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') save(); if (event.key === 'Escape') setEditing(false) }} /></label>
      <div className="flex gap-2">
        <button className={buttonStyle} disabled={busy || !text.trim()} onClick={save}>Save</button>
        <button className={buttonStyle} disabled={busy} onClick={() => { setText(item.text); setEditing(false) }}>Cancel</button>
      </div>
    </li>
  }
  return <li className="flex flex-wrap items-center justify-between gap-3 rounded border border-theme-border p-3 text-sm">
    <p className="min-w-[12rem] flex-1 break-words">{item.text}</p>
    <div className="flex gap-2">
      <button className={buttonStyle} disabled={busy} aria-label={`Edit memory: ${item.text}`} onClick={() => setEditing(true)}>Edit</button>
      <button className={buttonStyle} disabled={busy} aria-label={`Delete memory: ${item.text}`} onClick={() => onDelete(item.id)}>Delete</button>
    </div>
  </li>
}

// What Portal remembers about the owner. Each change takes effect at once, outside the page's Save.
export default function PortalMemorySettings({ memory, busy, onAct }) {
  const [confirmClear, setConfirmClear] = useState(false)
  const settings = patch => onAct([['memory-settings', { enabled: memory.enabled, askFirst: memory.askFirst, ...patch }]], 'Memory settings saved.')
  const count = memory.items.length
  return <section className={cardStyle} aria-labelledby="integrations-memory">
    <div>
      <h3 id="integrations-memory" className="font-semibold">Memory</h3>
      <p className="text-sm text-theme-text-muted">Portal can remember facts and preferences you tell it, such as your diet, the people you mention or how you like answers, and use them in later chats. They stay on this computer.</p>
    </div>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={memory.enabled} disabled={busy} onChange={event => settings({ enabled: event.target.checked })} />Let Portal remember things about me</label>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!memory.askFirst} disabled={busy || !memory.enabled} onChange={event => settings({ askFirst: !event.target.checked })} />Save memories without asking me first</label>
    <p className="text-xs text-theme-text-muted">{memory.askFirst ? 'Portal asks in the chat before it keeps something.' : 'Portal keeps what it learns right away; you can still edit or delete it here.'} To keep one chat out of memory, open its ••• menu and choose “Don’t remember this chat”.</p>

    {memory.pending.length > 0 && <div className="space-y-2">
      <h4 className="text-sm font-medium">Waiting for your answer</h4>
      <ul className="space-y-2" aria-label="Memories waiting for your answer">{memory.pending.map(item => <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-theme-border p-3 text-sm">
        <p className="min-w-[12rem] flex-1 break-words">{item.text}</p>
        <div className="flex gap-2">
          <button className={buttonStyle} disabled={busy} onClick={() => onAct([['memory-decide', { id: item.id, decision: 'save' }]], 'Saved to memory.')}>Save</button>
          <button className={buttonStyle} disabled={busy} onClick={() => onAct([['memory-decide', { id: item.id, decision: 'discard' }]], 'Not saved.')}>Don’t save</button>
        </div>
      </li>)}</ul>
    </div>}

    <div className="space-y-2">
      <h4 className="text-sm font-medium">{count ? `Portal remembers ${count} ${count === 1 ? 'thing' : 'things'}` : 'Portal remembers nothing yet'}</h4>
      {count > 0 && <ul className="space-y-2" aria-label="Memories">{memory.items.map(item => <Fact key={`${item.id}:${item.updatedAt}`} item={item} busy={busy}
        onEdit={(id, text) => onAct([['memory-edit', { id, text }]], 'Memory changed.')}
        onDelete={id => onAct([['memory-delete', { id }]], 'Memory deleted.')} />)}</ul>}
    </div>
    {count > 0 && (confirmClear
      ? <div className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Forget everything">
          <span>Forget all {count} {count === 1 ? 'memory' : 'memories'}? This cannot be undone.</span>
          <button className={buttonStyle} disabled={busy} onClick={async () => { await onAct([['memory-clear']], 'Portal forgot everything it remembered.'); setConfirmClear(false) }}>Forget everything</button>
          <button className={buttonStyle} disabled={busy} onClick={() => setConfirmClear(false)}>Cancel</button>
        </div>
      : <button className={buttonStyle} disabled={busy} onClick={() => setConfirmClear(true)}>Forget everything…</button>)}
  </section>
}
