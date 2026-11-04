import { useState } from 'react'

const inputStyle = 'w-full rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:outline-none focus:ring-2 focus:ring-theme-accent'
const buttonStyle = 'rounded border border-theme-border px-3 py-2 text-sm hover:bg-white/5 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-theme-accent'
const cardStyle = 'rounded-xl border border-theme-border bg-theme-card p-5 space-y-4'
const DEFAULT_API = 'https://api.telegram.org'
// As the service saves it (integrations_telegram.py _api_root): a trailing slash is dropped, so
// "https://api.telegram.org/" is not an unsaved change that keeps Test disabled.
const savedApiRoot = value => value.trim().replace(/\/+$/, '')

// Chat with Portal from Telegram through the owner's own bot. Saved with its own button:
// the bot token is a secret that is sent only when typed.
export default function PortalTelegramSettings({ telegram, busy, onAct }) {
  const [enabled, setEnabled] = useState(telegram.enabled)
  const [ownerId, setOwnerId] = useState(telegram.ownerId)
  const [token, setToken] = useState('')
  const [apiRoot, setApiRoot] = useState(telegram.apiRoot)
  const [result, setResult] = useState(null)
  const changed = enabled !== telegram.enabled || ownerId.trim() !== telegram.ownerId || token !== '' || (savedApiRoot(apiRoot) || DEFAULT_API) !== telegram.apiRoot
  const save = async () => {
    setResult(null)
    const outcome = await onAct([['telegram-save', { enabled, ownerId: ownerId.trim(), apiRoot: savedApiRoot(apiRoot) || DEFAULT_API, ...(token ? { token } : {}) }]])
    if (outcome.ok) setToken('')
    setResult(outcome)
  }
  const test = async () => { setResult(null); setResult(await onAct([['telegram-test']])) }
  return <section className={cardStyle} aria-labelledby="integrations-telegram">
    <div>
      <h3 id="integrations-telegram" className="font-semibold">Telegram</h3>
      <p className="text-sm text-theme-text-muted">Chat with Portal from Telegram when you are away from this page. Portal answers only your own Telegram account; messages from anyone else are ignored.</p>
    </div>
    <ol className="list-decimal space-y-1 pl-5 text-sm text-theme-text-muted">
      <li>In Telegram, message <span className="text-theme-text">@BotFather</span>, send /newbot and follow its steps. It gives you a bot token.</li>
      <li>Message <span className="text-theme-text">@userinfobot</span> to see your Telegram user ID, a number.</li>
      <li>Paste both here, turn Telegram on and save. Then press Test and message your bot.</li>
    </ol>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} disabled={busy} onChange={event => setEnabled(event.target.checked)} />Let me chat with Portal from Telegram</label>
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-sm">Bot token<input className={inputStyle} type="password" autoComplete="off" value={token} disabled={busy} spellCheck={false}
        placeholder={telegram.hasToken ? 'Saved. Type to replace.' : '123456789:AAH…'} onChange={event => setToken(event.target.value)} /></label>
      <label className="text-sm">Your Telegram user ID<input className={inputStyle} inputMode="numeric" value={ownerId} disabled={busy} spellCheck={false}
        placeholder="123456789" onChange={event => setOwnerId(event.target.value)} /></label>
    </div>
    <details className="text-sm">
      <summary className="cursor-pointer text-theme-text-muted">Advanced</summary>
      <label className="mt-2 block">Telegram API address<input className={inputStyle} value={apiRoot} disabled={busy} spellCheck={false}
        onChange={event => setApiRoot(event.target.value)} /></label>
      <p className="mt-1 text-xs text-theme-text-muted">Change it only for your own Telegram Bot API server.</p>
    </details>
    <div className="flex flex-wrap items-center gap-2">
      <button className={buttonStyle} disabled={busy || !changed} onClick={save}>Save Telegram</button>
      <button className={buttonStyle} disabled={busy || !telegram.hasToken || changed} onClick={test}>Test</button>
      {result && <span role={result.ok ? 'status' : 'alert'} className={`min-w-0 break-words text-sm ${result.ok ? 'text-theme-text-muted' : 'text-red-400'}`}>{result.message}</span>}
    </div>
    {telegram.enabled && telegram.bot && <p className="text-sm">Portal answers you at <span className="font-medium">{telegram.bot}</span>{telegram.lastMessageAt ? `, last message ${new Date(telegram.lastMessageAt * 1000).toLocaleString()}` : ''}.</p>}
    {telegram.enabled && telegram.lastError && <p role="alert" className="text-sm text-red-400 break-words">{telegram.lastError}</p>}
    {telegram.refusedCount > 0 && <p className="text-xs text-theme-text-muted">Someone other than you wrote to your bot; Portal ignored it.</p>}
    <p className="text-xs text-theme-text-muted">Telegram carries these messages on its servers, so keep private matters to this page. Approvals still happen in Settings.</p>
  </section>
}
