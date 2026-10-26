import { useCallback, useEffect, useRef, useState } from 'react'
import { Problem } from './GoogleConnectWizard'
import { activityLine, undoLine, undoNotice } from './calendarChangeView'
import { listCalendarActivity, undoCalendarChange } from './useConnectedApps'

const button = 'rounded-lg border border-white/20 px-3 py-2 disabled:opacity-40'
// A change the calendar summary hasn't caught up with yet becomes undoable at the next sync.
const SYNC_WAIT = 30000

function when(value) {
  return value ? new Date(value).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : null
}

// The private changes Portal made by itself this week, newest first, each with Undo.
export default function CalendarActivity({onChanged}) {
  const [list, setList] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const [notice, setNotice] = useState('')
  const latest = useRef(0)

  const load = useCallback(async () => {
    const version = ++latest.current
    try {
      const value = await listCalendarActivity()
      if (version === latest.current) setList(value)
    } catch (error) {
      if (version === latest.current) setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    }
  }, [])

  useEffect(() => {
    void load()
    return () => { latest.current++ }
  }, [load])

  const syncing = Boolean(list?.changes?.some(entry => entry.undo === 'syncing'))
  useEffect(() => {
    if (!syncing) return undefined
    const timer = setTimeout(() => { void load() }, SYNC_WAIT)
    return () => clearTimeout(timer)
  }, [syncing, list, load])

  async function undo(entry) {
    setBusy(true)
    setProblem(null)
    setNotice('')
    try {
      const result = await undoCalendarChange(entry)
      setNotice(undoNotice(result.outcome, entry))
    } catch (error) {
      setProblem({message: error.message, fix: null, help: Boolean(error.help)})
    } finally {
      setBusy(false)
      await load()
      onChanged()
    }
  }

  const changes = list?.changes ?? []
  return <section aria-labelledby="calendar-activity-title" className="space-y-3">
    <h4 id="calendar-activity-title" className="font-semibold">Changes Portal made by itself</h4>
    {list && !changes.length ? <p className="text-sm text-theme-text-muted">Nothing this week. Private events Portal adds or moves appear here, with Undo.</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {problem ? <Problem problem={problem} /> : null}
    {changes.length ? <ul className="space-y-2">
      {changes.map(entry => <li key={entry.proposalId} className="space-y-1 rounded-lg border border-white/10 p-3 text-sm">
        <p>{activityLine(entry)}</p>
        {entry.appliedAt ? <p className="text-theme-text-muted">{when(entry.appliedAt)}</p> : null}
        {entry.undo === 'available'
          ? <button type="button" disabled={busy} className={button} onClick={() => { void undo(entry) }}>Undo</button>
          : <p className="text-theme-text-muted">{undoLine(entry)}</p>}
      </li>)}
    </ul> : null}
    {list?.more ? <p className="text-sm text-theme-text-muted">{list.more === 1 ? 'One older change this week isn’t shown.' : `${list.more} older changes this week aren’t shown.`}</p> : null}
  </section>
}
