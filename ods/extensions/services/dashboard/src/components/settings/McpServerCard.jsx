import { useState } from 'react'
import { ENV_NAME, POLICY_LABELS, savedTools, toolEffect } from './portalIntegrationsForm'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'

function ToolRow({ tool, policy, disabled, onPolicy }) {
  return <li className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm">
    <div className="min-w-[12rem] flex-1">
      <p className="font-medium break-words">{tool.title || tool.name} <span className="text-theme-text-muted font-normal">· {toolEffect(tool)}</span>
        {tool.new && <span className="ml-2 rounded border border-theme-accent px-1 text-xs text-theme-accent">New</span>}</p>
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

// Secret environment variables for a local server, such as GITHUB_TOKEN; values are never shown back.
function EnvVariables({ server, saved, values, busy, onChange, onValue }) {
  const [name, setName] = useState('')
  const valid = ENV_NAME.test(name) && name !== 'PATH' && !server.envNames.includes(name)
  const add = () => { onChange({ envNames: [...server.envNames, name] }); setName('') }
  return <fieldset className="space-y-2 md:col-span-2">
    <legend className="text-sm">Environment variables (kept secret)</legend>
    {server.envNames.map(variable => <div key={variable} className="flex flex-wrap items-end gap-2">
      <label className="min-w-[12rem] flex-1 text-sm">{variable}
        <input className={inputStyle} type="password" autoComplete="off" value={values[variable] || ''} disabled={busy}
          placeholder={saved?.hasEnv?.[variable] ? 'Saved. Type to replace.' : 'Value'} onChange={event => onValue(variable, event.target.value)} /></label>
      <button className={buttonStyle} disabled={busy} onClick={() => onChange({ envNames: server.envNames.filter(item => item !== variable) })}>Remove {variable}</button>
    </div>)}
    <div className="flex flex-wrap items-end gap-2">
      <label className="min-w-[12rem] flex-1 text-sm">New variable name
        <input className={inputStyle} value={name} disabled={busy} spellCheck={false} placeholder="API_KEY" onChange={event => setName(event.target.value.trim())} /></label>
      <button className={buttonStyle} disabled={busy || !valid} onClick={add}>Add variable</button>
    </div>
  </fieldset>
}

function ProcessStatus({ process }) {
  if (!process) return null
  return <div className="space-y-1 text-sm">
    <p className="text-theme-text-muted">{process.running ? 'Running. It stops after 10 minutes without use and starts again when needed.' : 'Not running. It starts when Portal or a check uses it.'}</p>
    {process.lastError && <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-theme-border p-2 text-xs" aria-label="Last error output">{process.lastError}</pre>}
  </div>
}

export default function McpServerCard({ server, saved, process, token, envValues, busy, onChange, onToken, onEnvValue, onCheck, onTest, onRestart, onRemove }) {
  const changedSecrets = Boolean(token || Object.values(envValues || {}).some(Boolean))
  const tools = changedSecrets ? null : savedTools(saved, server)
  const isHttp = server.kind === 'http'
  const newTools = tools ? tools.filter(tool => tool.new).length : 0
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
        : <>
            <label className="text-sm md:col-span-2">Command<input className={inputStyle} value={server.command} disabled={busy} spellCheck={false} placeholder="npx -y @modelcontextprotocol/server-everything" onChange={event => onChange({ command: event.target.value })} /></label>
            <p className="text-xs text-theme-text-muted md:col-span-2">If the program is not found, give its full path, as <code>which npx</code> shows it in a terminal.</p>
            <label className="text-sm md:col-span-2">Working folder (optional, full path)<input className={inputStyle} value={server.workingFolder} disabled={busy} spellCheck={false} placeholder="Your home folder" onChange={event => onChange({ workingFolder: event.target.value })} /></label>
            <EnvVariables server={server} saved={saved} values={envValues} busy={busy} onChange={onChange} onValue={onEnvValue} />
            <p role="note" className="text-sm md:col-span-2">This program runs on this computer with your permissions and can use your files. Only add programs you trust.</p>
          </>}
      {isHttp && <label className="text-sm md:col-span-2">{server.preset === 'home-assistant' ? 'Long-lived access token' : 'Access token (optional)'}
        <input className={inputStyle} type="password" autoComplete="off" value={token} disabled={busy}
          placeholder={saved?.hasToken ? 'Saved. Type to replace.' : ''} onChange={event => onToken(event.target.value)} /></label>}
    </div>
    {server.preset === 'home-assistant' && <p className="text-xs text-theme-text-muted">In Home Assistant, add the “Model Context Protocol Server” integration, then create a long-lived access token on your profile page. The address ends in /api/mcp.</p>}
    {!isHttp && <ProcessStatus process={process} />}
    <div className="flex flex-wrap items-center gap-2">
      <button className={buttonStyle} disabled={busy} onClick={onCheck}>Check tools</button>
      <button className={buttonStyle} disabled={busy} onClick={onTest}>Test connection</button>
      {!isHttp && <button className={buttonStyle} disabled={busy || !process?.running} onClick={onRestart}>Restart</button>}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={server.enabled} disabled={busy || !tools && !server.enabled} onChange={event => onChange({ enabled: event.target.checked })} />
        Let Portal use this server
      </label>
      <button className={buttonStyle} disabled={busy} onClick={onRemove}>Remove {server.name}</button>
    </div>
    {tools
      ? <div className="space-y-2">
          <p className="text-sm text-theme-text-muted">{tools.length} tools. Choose what Portal may do: “Ask first” waits for your approval each time.</p>
          <p className="text-xs text-theme-text-muted">Behavior labels are supplied by this server, not enforced by Portal. Only connect servers you trust.</p>
          {newTools > 0 && <p role="status" className="text-sm">{newTools === 1 ? '1 new tool' : `${newTools} new tools`} since the last check. New tools start Off; save to confirm your choices.</p>}
          <ul className="divide-y divide-theme-border" aria-label={`Tools of ${server.name}`}>
            {tools.map(tool => <ToolRow key={tool.name} tool={tool} policy={server.policies[tool.name] || tool.policy} disabled={busy} onPolicy={policy => setPolicy(tool.name, policy)} />)}
          </ul>
        </div>
      : <p className="text-sm text-theme-text-muted">Check tools to see what this server can do before turning it on.</p>}
  </li>
}
