import { useCallback, useEffect, useRef, useState } from 'react'
import { Problem } from './GoogleConnectWizard'
import { changeActions, changeRows, changeTitle, guestNotice, outcomeNotice, stateLine } from './calendarChangeView'
import { approveCalendarChange, checkCalendarChange, declineCalendarChange, listCalendarChanges } from './useConnectedApps'

const button = 'rounded-lg bg-white/[0.06] px-3 py-2 hover:bg-white/[0.1] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-theme-accent'
const LABELS = {approve: 'Approve', decline: 'Decline', check: 'Check again', dismiss: 'Dismiss'}
const RUN = {approve: approveCalendarChange, decline: declineCalendarChange, dismiss: declineCalendarChange,
  check: checkCalendarChange}

function Change({entry, busy, act}) {
  const id = `calendar-change-${entry.proposalId}`
  const rows = changeRows(entry)
  const line = stateLine(entry)
  const guests = guestNotice(entry)
  return <article aria-labelledby={id} className="space-y-2 rounded-lg bg-white/[0.025] p-4">
    <h4 id={id} className="font-medium">{changeTitle(entry)}</h4>
    {rows.length ? <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([label, value]) => [<dt key={`${label}-label`} className="text-theme-text-muted">{label}</dt>,
        <dd key={`${label}-value`} className="whitespace-pre-wrap break-words">{value}</dd>])}
    </dl> : null}
    {guests ? <p className="text-sm">{guests}</p> : null}
    {line ? <p className="text-sm">{line}</p> : null}
    <div className="flex flex-wrap gap-3">
      {changeActions(entry).map(name => <button key={name} type="button" disabled={busy} className={button}
        onClick={() => act(name, entry)}>{LABELS[name]}</button>)}
    </div>
  </article>
}

// The calendar changes Portal suggested, each made only when the owner approves exactly what is shown.
export default function CalendarChanges({waiting, writable, onChanged}) {
  const [list, setList] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [notice, setNotice] = useState('')
  const latest = useRef(0)

  const load = useCallback(async () => {
    const version = ++latest.current
    try {
      const value = await listCalendarChanges()
      if (version === latest.current) setList(value)
    } catch (error) {
      if (version === latest.current) setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    }
  }, [])

  // Status counts waiting changes; a new count means a new list.
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
      setNotice(outcomeNotice(result.outcome, entry))
    } catch (error) {
      setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    } finally {
      setBusy(false)
      await load()
      onChanged()
    }
  }

  const changes = list?.proposals ?? []
  if (!changes.length && !notice && !problem) {
    return writable ? <p className="text-sm text-theme-text-muted">Calendar changes Portal suggests wait here until you approve them.</p> : null
  }
  return <section aria-labelledby="calendar-changes-title" className="space-y-3">
    <h4 id="calendar-changes-title" className="font-semibold">Calendar changes from Portal</h4>
    {changes.length ? <p className="text-sm text-theme-text-muted">Nothing changes on your calendar until you approve it. Decline anything you don&apos;t want.</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {problem ? <Problem problem={problem} /> : null}
    {changes.map(entry => <Change key={entry.proposalId} entry={entry} busy={busy} act={act} />)}
    {list?.more ? <p className="text-sm text-theme-text-muted">{list.more === 1
      ? "One more change waits. It appears here once you've handled these."
      : `${list.more} more changes wait. They appear here once you've handled these.`}</p> : null}
  </section>
}
