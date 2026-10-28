import { POLICY_LABELS, savedTools, toolEffect } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'

function ToolRow({ tool, policy, disabled, onPolicy }) {
  return <li className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm">
    <div className="min-w-[12rem] flex-1">
      <p className="font-medium break-words">{tool.title || tool.name} <span className="text-theme-text-muted font-normal">· {toolEffect(tool)}</span></p>
      {tool.description && <p className="text-theme-text-muted break-words">{tool.description}</p>}
    </div>
    <label className="text-sm">
      <span className="sr-only">Permission for {tool.name}</span>
      <select className={inputStyle} value={policy} disabled={disabled} onChange={event => onPolicy(event.target.value)}>
        {Object.entries(POLICY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
  </li>
}

export default function McpServerCard({ server, saved, token, busy, onChange, onToken, onCheck, onRemove }) {
  const tools = savedTools(saved, server)
  const isHttp = server.kind === 'http'
  const setPolicy = (name, policy) => onChange({ policies: { ...server.policies, [name]: policy } })
  return <li className="rounded-lg border border-theme-border p-4 space-y-3" aria-label={`MCP server ${server.name}`}>
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-sm">Name<input className={inputStyle} value={server.name} disabled={busy} onChange={event => onChange({ name: event.target.value })} /></label>
      <label className="text-sm">Connection
        <select className={inputStyle} value={server.kind} disabled={busy || server.preset === 'home-assistant'} onChange={event => onChange({ kind: event.target.value })}>
          <option value="http">Web address (Streamable HTTP)</option>
          <option value="stdio">Command on this computer (stdio)</option>
        </select>
      </label>
      {isHttp
        ? <label className="text-sm md:col-span-2">Address<input className={inputStyle} value={server.url} disabled={busy} spellCheck={false} placeholder="http://127.0.0.1:8123/api/mcp" onChange={event => onChange({ url: event.target.value })} /></label>
        : <label className="text-sm md:col-span-2">Command<input className={inputStyle} value={server.command} disabled={busy} spellCheck={false} placeholder="npx -y @modelcontextprotocol/server-everything" onChange={event => onChange({ command: event.target.value })} /></label>}
      {isHttp && <label className="text-sm md:col-span-2">{server.preset === 'home-assistant' ? 'Long-lived access token' : 'Access token (optional)'}
        <input className={inputStyle} type="password" autoComplete="off" value={token} disabled={busy}
          placeholder={saved?.hasToken ? 'Saved. Type to replace.' : ''} onChange={event => onToken(event.target.value)} /></label>}
    </div>
    {server.preset === 'home-assistant' && <p className="text-xs text-theme-text-muted">In Home Assistant, add the “Model Context Protocol Server” integration, then create a long-lived access token on your profile page. The address ends in /api/mcp.</p>}
    <div className="flex flex-wrap items-center gap-2">
      <button className={buttonStyle} disabled={busy} onClick={onCheck}>Check tools</button>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={server.enabled} disabled={busy || !tools} onChange={event => onChange({ enabled: event.target.checked })} />
        Let Portal use this server
      </label>
      <button className={buttonStyle} disabled={busy} onClick={onRemove}>Remove {server.name}</button>
    </div>
    {tools
      ? <div className="space-y-2">
          <p className="text-sm text-theme-text-muted">{tools.length} tools. Choose what Portal may do: “Ask first” waits for your approval each time.</p>
          <ul className="divide-y divide-theme-border" aria-label={`Tools of ${server.name}`}>
            {tools.map(tool => <ToolRow key={tool.name} tool={tool} policy={server.policies[tool.name] || tool.policy} disabled={busy} onPolicy={policy => setPolicy(tool.name, policy)} />)}
          </ul>
        </div>
      : <p className="text-sm text-theme-text-muted">Check tools to see what this server can do before turning it on.</p>}
  </li>
}
