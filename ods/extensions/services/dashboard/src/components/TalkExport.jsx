import { useState } from 'react'

export default function TalkExport({ messages, busy }) {
  const [format, setFormat] = useState('md')
  const transcript = messages.filter(message => message.id !== 'welcome').map(message => ({
    role: message.role,
    text: message.text || '',
    status: message.status || 'done',
  }))
  function download() {
    if (busy || transcript.length === 0) return
    const exportedAt = new Date().toISOString()
    const content = format === 'json'
      ? JSON.stringify({ schema: 'ods.talk-transcript.v1', exportedAt, messages: transcript }, null, 2)
      : `# ODS Talk\n\nExported: ${exportedAt}\n\n${transcript.map(message => `## ${message.role === 'user' ? 'You' : 'ODS'} (${message.status})\n\n${message.text}`).join('\n\n---\n\n')}\n`
    const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `ods-talk-${exportedAt.slice(0, 10)}.${format}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-600">
      <select aria-label="Transcript format" value={format} onChange={event => setFormat(event.target.value)} className="rounded border border-zinc-200 bg-white p-2">
        <option value="md">Markdown</option><option value="json">JSON</option>
      </select>
      <button type="button" disabled={busy || transcript.length === 0} onClick={download} className="rounded border border-zinc-200 bg-white p-2 disabled:opacity-50">Export conversation</button>
      <span>Text from this tab only; attachment files are not included.</span>
    </div>
  )
}
