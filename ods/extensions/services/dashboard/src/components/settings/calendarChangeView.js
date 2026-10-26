// Calendar changes Portal suggested, as the owner reads them before approving.
// Pure: one entry of the dashboard API's list in, words out. Every value the
// change would send to Google is shown; the hash that approves it is never shown.

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/

// A date-only value is a calendar day, not midnight UTC.
function day(value) {
  const match = DATE.exec(value ?? '')
  return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null
}

function format(date, options) {
  return new Intl.DateTimeFormat([], options).format(date)
}

const DAY = {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'}
const MOMENT = {...DAY, hour: 'numeric', minute: '2-digit'}
const TIME = {hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}

// "Tue, Oct 27, 2026, 3:00 PM – 4:00 PM CDT", or the days of an all-day event.
export function changeWhen(start, end, allDay) {
  if (!start || !end) return null
  if (allDay) {
    const first = day(start), after = day(end)
    if (!first || !after) return `${start} – ${end}`
    // Google ends an all-day event on the morning after its last day.
    const last = new Date(after.getFullYear(), after.getMonth(), after.getDate() - 1)
    return last > first ? `All day, ${format(first, DAY)} – ${format(last, DAY)}` : `All day, ${format(first, DAY)}`
  }
  const first = new Date(start), last = new Date(end)
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) return `${start} – ${end}`
  const sameDay = first.toDateString() === last.toDateString()
  return sameDay ? `${format(first, MOMENT)} – ${format(last, TIME)}`
    : `${format(first, MOMENT)} – ${format(last, {...MOMENT, timeZoneName: 'short'})}`
}

const quoted = text => `“${text}”`

// The card's heading: what approving does, in a few words.
export function changeTitle(entry) {
  const name = entry.event?.title ?? entry.change?.summary
  if (entry.action === 'create') return `Add ${entry.change?.summary ? quoted(entry.change.summary) : 'an event'}`
  if (entry.action === 'update') return `Change ${name ? quoted(name) : 'an event'}`
  if (entry.action === 'delete') return `Delete ${name ? quoted(name) : 'an event'}`
  return 'A calendar change'
}

// [label, value] rows: every value the change sends, and the event it changes.
export function changeRows(entry) {
  const change = entry.change
  if (!change) return []
  const rows = []
  const now = entry.event ? changeWhen(entry.event.start, entry.event.end, entry.event.allDay) : null
  const next = changeWhen(change.start, change.end, change.allDay)
  if (entry.action === 'create') {
    if (next) rows.push(['When', next])
  } else {
    if (now) rows.push([entry.action === 'delete' ? 'When' : 'Now', now])
    if (next) rows.push(['New time', next])
    if (entry.action === 'update' && change.summary !== null && change.summary !== entry.event?.title) rows.push(['New title', change.summary])
  }
  if (change.location !== null) rows.push([entry.action === 'create' ? 'Where' : 'New place', change.location || 'No place'])
  if (change.attendees !== null) {
    rows.push([entry.action === 'create' ? 'Guests' : 'New guest list', change.attendees.length ? change.attendees.join(', ') : 'No guests'])
  }
  if (change.description !== null) rows.push([entry.action === 'create' ? 'Notes' : 'New notes', change.description || 'No notes'])
  return rows
}

// Whether Google writes to anyone when the owner approves.
export function guestNotice(entry) {
  if (!entry.change) return null
  if (entry.change.sendUpdates === 'all') return "Google emails the event's guests when you approve."
  return entry.change.attendees?.length ? "Google won't email the guests." : null
}

// The line a change shows when it is not simply waiting.
export function stateLine(entry) {
  if (entry.state === 'invalid') return "ODS can't read this suggestion, so it can't be made. Decline it, and ask Portal again if you still want it."
  if (entry.tooLarge) return "This change is too long to show here, so it can't be approved here. Decline it, and ask Portal for a shorter one."
  if (entry.state === 'unconfirmed') {
    return entry.action === 'create'
      ? "Google hasn't confirmed this change, so ODS won't send it again. Check again to see whether the event was added."
      : "Google didn't confirm this change, so ODS won't send it again. Check your calendar to see whether it happened, then dismiss it."
  }
  if (entry.state === 'applied') return 'Done. This change is on your calendar.'
  if (entry.state === 'rejected') return 'Google refused this change: the event changed after Portal suggested it. Ask Portal again if you still want it.'
  if (entry.state === 'not-applied') return "This change wasn't made: the event changed after Portal suggested it. Ask Portal again if you still want it."
  return null
}

// The actions a change offers: approve only what is shown whole and still waits.
export function changeActions(entry) {
  if (entry.state === 'waiting' && !entry.tooLarge) return ['approve', 'decline']
  if (entry.state === 'unconfirmed') return entry.action === 'create' ? ['check', 'dismiss'] : ['dismiss']
  return [entry.state === 'waiting' || entry.state === 'invalid' ? 'decline' : 'dismiss']
}

// What the owner reads after acting on a change.
export function outcomeNotice(outcome, entry) {
  if (outcome === 'declined') return entry.state === 'waiting' || entry.state === 'invalid' ? 'Declined. Nothing changed on your calendar.' : 'Dismissed.'
  if (outcome === 'applied') {
    return entry.action === 'delete' ? 'Done. The event is deleted from your calendar.' : 'Done. Your calendar has the change.'
  }
  if (outcome === 'unconfirmed') return "Google hasn't confirmed the change yet. ODS won't send it again; check your calendar in a minute."
  if (outcome === 'waiting') return 'Nothing was sent for this change yet.'
  return stateLine({...entry, state: outcome})
}
