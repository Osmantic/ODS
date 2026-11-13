import { describe, expect, it } from 'vitest'
import { changeActions, changeRows, changeTitle, changeWhen, guestNotice, outcomeNotice, stateLine } from './calendarChangeView'
import { CHANGE, MOVE, REMOVE, UNREADABLE } from './connectedAppsFixtures'

describe('what a change does, in words', () => {
  it('names the event each change adds, changes or deletes', () => {
    expect(changeTitle(CHANGE)).toBe('Add “Dentist”')
    expect(changeTitle(MOVE)).toBe('Change “Team sync”')
    expect(changeTitle(REMOVE)).toBe('Delete “Lunch”')
    expect(changeTitle({...MOVE, event: null})).toBe('Change an event')
    expect(changeTitle(UNREADABLE)).toBe('A calendar change')
  })

  it('shows every value a new event sends to Google', () => {
    const rows = Object.fromEntries(changeRows(CHANGE))
    expect(Object.keys(rows)).toEqual(['When', 'Where', 'Guests', 'Notes'])
    expect(rows.Where).toBe('12 Main St')
    expect(rows.Guests).toBe('pat@example.com')
    expect(rows.Notes).toBe('Bring the form')
    expect(guestNotice(CHANGE)).toBe("Google emails the event's guests when you approve.")
  })

  it('shows a move as the time now and the new time, and leaves untouched fields out', () => {
    const rows = changeRows(MOVE)
    expect(rows.map(([label]) => label)).toEqual(['Now', 'New time'])
    const time = value => new Date(value).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
    expect(rows[0][1]).toContain(time(MOVE.event.start))
    expect(rows[1][1]).toContain(time(MOVE.change.start))
    expect(guestNotice(MOVE)).toBeNull()
  })

  it('tells an emptied guest list or notes apart from an untouched one', () => {
    const cleared = {...MOVE, change: {...MOVE.change, attendees: [], description: '', start: null, end: null}}
    expect(Object.fromEntries(changeRows(cleared))).toEqual({Now: changeRows(MOVE)[0][1], 'New guest list': 'No guests',
      'New notes': 'No notes'})
  })

  it('shows a deletion by the event it removes', () => {
    expect(changeRows(REMOVE)).toEqual([['When', expect.stringMatching(/^All day, /)]])
  })
})

describe('when', () => {
  it('reads an all-day event by its days, not midnight UTC', () => {
    const one = changeWhen('2026-10-29', '2026-10-30', true)
    expect(one).toMatch(/^All day, .*29/)
    expect(one).not.toMatch(/–/)
    expect(changeWhen('2026-10-29', '2026-11-01', true)).toMatch(/29.* – .*31/)
  })

  it('reads a timed event in the browser\'s time, with the zone', () => {
    expect(changeWhen('2026-10-27T15:00:00Z', '2026-10-27T16:00:00Z', false)).toMatch(/ – /)
    expect(changeWhen(null, '2026-10-27T16:00:00Z', false)).toBeNull()
    expect(changeWhen('soon', 'later', false)).toBe('soon – later')
  })
})

describe('what the owner can do with each change', () => {
  it('approves only a change shown whole that still waits', () => {
    expect(changeActions(CHANGE)).toEqual(['approve', 'decline'])
    expect(changeActions({...CHANGE, tooLarge: true, sha256: null, change: null})).toEqual(['decline'])
    expect(changeActions(UNREADABLE)).toEqual(['decline'])
  })

  it('checks an unconfirmed new event again, and only dismisses other unconfirmed changes', () => {
    expect(changeActions({...CHANGE, state: 'unconfirmed'})).toEqual(['check', 'dismiss'])
    expect(changeActions(REMOVE)).toEqual(['dismiss'])
    expect(stateLine(REMOVE)).toMatch(/won't send it again. Check your calendar/)
  })

  it('says plainly how each change ended, and never claims a change Google did not confirm', () => {
    expect(outcomeNotice('applied', CHANGE)).toBe('Done. Your calendar has the change.')
    expect(outcomeNotice('applied', REMOVE)).toBe('Done. The event is deleted from your calendar.')
    expect(outcomeNotice('declined', CHANGE)).toBe('Declined. Nothing changed on your calendar.')
    expect(outcomeNotice('declined', REMOVE)).toBe('Dismissed.')
    expect(outcomeNotice('unconfirmed', CHANGE)).toMatch(/hasn't confirmed/)
    expect(outcomeNotice('rejected', MOVE)).toMatch(/^Google refused this change/)
    for (const outcome of ['applied', 'declined', 'unconfirmed', 'rejected', 'not-applied', 'waiting']) {
      expect(outcomeNotice(outcome, CHANGE)).not.toMatch(/error|fail/i)
    }
  })
})
