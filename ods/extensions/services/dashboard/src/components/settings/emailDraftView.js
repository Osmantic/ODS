// Gmail drafts Portal wrote, as the owner reads them before approving.
// Pure: one entry of the dashboard API's list in, words out. Every value the
// draft would hold is shown; the hash that approves it is never shown.

const quoted = text => `“${text}”`

// The card's heading: what approving does, in a few words.
export function draftTitle(entry) {
  const subject = entry.draft?.subject
  return subject ? `Draft ${quoted(subject)}` : 'A Gmail draft'
}

// [label, value] rows: who the draft is for, its subject, and the message it answers.
export function draftRows(entry) {
  const draft = entry.draft
  if (!draft) return []
  const rows = [['To', draft.to.join(', ')]]
  if (draft.cc.length) rows.push(['Cc', draft.cc.join(', ')])
  rows.push(['Subject', draft.subject || 'No subject'])
  if (draft.replyTo) {
    const {from, subject} = draft.replyTo
    rows.push(['In reply to', from || subject
      ? [subject ? quoted(subject) : null, from ? `from ${from}` : null].filter(Boolean).join(' ')
      : 'A message in your Gmail'])
  }
  return rows
}

// The line a draft shows when it is not simply waiting.
export function draftStateLine(entry) {
  if (entry.state === 'invalid') return "ODS can't read this draft, so it can't be created. Decline it, and ask Portal again if you still want it."
  if (entry.tooLarge) return "This draft is too long to show here, so it can't be approved here. Decline it, and ask Portal for a shorter one."
  if (entry.state === 'unconfirmed') return "Gmail hasn't confirmed this draft, so ODS won't create it again. Check again to see whether it's in your Drafts."
  if (entry.state === 'applied') return 'Done. The draft is in your Gmail Drafts. Open Gmail to read it over and send it.'
  if (entry.state === 'rejected') return 'Gmail refused this draft. Ask Portal again if you still want it.'
  if (entry.state === 'not-applied') return "This draft wasn't created. Ask Portal again if you still want it."
  return null
}

// The actions a draft offers: approve only what is shown whole and still waits.
export function draftActions(entry) {
  if (entry.state === 'waiting' && !entry.tooLarge) return ['approve', 'decline']
  if (entry.state === 'unconfirmed') return ['check', 'dismiss']
  return [entry.state === 'waiting' || entry.state === 'invalid' ? 'decline' : 'dismiss']
}

// What the owner reads after acting on a draft.
export function draftOutcomeNotice(outcome, entry) {
  if (outcome === 'declined') return entry.state === 'waiting' || entry.state === 'invalid' ? 'Declined. Nothing was written to Gmail.' : 'Dismissed.'
  if (outcome === 'unconfirmed') return "Gmail hasn't confirmed the draft yet. ODS won't create it again; check your Drafts folder in a minute."
  if (outcome === 'waiting') return 'Nothing was sent to Gmail for this draft yet.'
  return draftStateLine({...entry, state: outcome, tooLarge: false})
}
