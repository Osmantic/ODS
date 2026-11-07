import { useCallback, useEffect, useRef, useState } from 'react'
import { Problem } from './GoogleConnectWizard'
import { draftActions, draftOutcomeNotice, draftRows, draftStateLine, draftTitle } from './emailDraftView'
import { approveGmailDraft, checkGmailDraft, declineGmailDraft, listGmailDrafts } from './useConnectedApps'

const button = 'rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'
const LABELS = {approve: 'Create draft', decline: 'Decline', check: 'Check again', dismiss: 'Dismiss'}
const RUN = {approve: approveGmailDraft, decline: declineGmailDraft, dismiss: declineGmailDraft, check: checkGmailDraft}

function Draft({entry, busy, act}) {
  const id = `gmail-draft-${entry.proposalId}`
  const rows = draftRows(entry)
  const line = draftStateLine(entry)
  return <article aria-labelledby={id} className="space-y-2 rounded-lg bg-white/[0.025] p-4">
    <h4 id={id} className="font-medium">{draftTitle(entry)}</h4>
    {rows.length ? <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([label, value]) => [<dt key={`${label}-label`} className="text-theme-text-muted">{label}</dt>,
        <dd key={`${label}-value`} className="break-words">{value}</dd>])}
    </dl> : null}
    {entry.draft ? <p aria-label="Message" className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded bg-white/[0.025] p-3 text-sm">{entry.draft.body}</p> : null}
    {line ? <p className="text-sm">{line}</p> : null}
    <div className="flex flex-wrap gap-3">
      {draftActions(entry).map(name => <button key={name} type="button" disabled={busy} className={button}
        onClick={() => act(name, entry)}>{LABELS[name]}</button>)}
    </div>
  </article>
}

// The Gmail drafts Portal wrote, each created only when the owner approves exactly what is shown.
// Portal never sends email: an approved draft waits in Gmail for the owner to send.
export default function EmailDrafts({waiting, writable, onChanged}) {
  const [list, setList] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [notice, setNotice] = useState('')
  const latest = useRef(0)

  const load = useCallback(async () => {
    const version = ++latest.current
    try {
      const value = await listGmailDrafts()
      if (version === latest.current) setList(value)
    } catch (error) {
      if (version === latest.current) setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    }
  }, [])

  // Status counts waiting drafts; a new count means a new list.
  useEffect(() => {
    if (waiting > 0) void load()
    else setList(null)
  }, [waiting, load])
  useEffect(() => () => { latest.current++ }, [])

  async function act(name, entry) {
    setBusy(true)
    setProblem(null)
    setNotice('')
    try {
      const result = await RUN[name](entry)
      setNotice(draftOutcomeNotice(result.outcome, entry))
    } catch (error) {
      setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    } finally {
      setBusy(false)
      await load()
      onChanged()
    }
  }

  const drafts = list?.drafts ?? []
  if (!drafts.length && !notice && !problem) {
    return writable ? <p className="text-sm text-theme-text-muted">Gmail drafts Portal writes wait here until you approve them. Portal never sends email.</p> : null
  }
  return <section aria-labelledby="gmail-drafts-title" className="space-y-3">
    <h4 id="gmail-drafts-title" className="font-semibold">Gmail drafts from Portal</h4>
    {drafts.length ? <p className="text-sm text-theme-text-muted">Nothing reaches Gmail until you approve it. An approved draft waits in your Gmail Drafts, and you send it from there.</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {problem ? <Problem problem={problem} /> : null}
    {drafts.map(entry => <Draft key={entry.proposalId} entry={entry} busy={busy} act={act} />)}
    {list?.more ? <p className="text-sm text-theme-text-muted">{list.more === 1
      ? "One more draft waits. It appears here once you've handled these."
      : `${list.more} more drafts wait. They appear here once you've handled these.`}</p> : null}
  </section>
}
