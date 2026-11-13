import { describe, expect, it } from 'vitest'
import { draftActions, draftOutcomeNotice, draftRows, draftStateLine, draftTitle } from './emailDraftView'
import { DRAFT, REPLY, UNREADABLE_DRAFT } from './connectedAppsFixtures'

describe('Gmail drafts as the owner reads them', () => {
  it('shows who a draft is for, its subject, and the message a reply answers', () => {
    expect(draftTitle(DRAFT)).toBe('Draft “Thursday plan”')
    expect(draftRows(DRAFT)).toEqual([['To', 'pat@example.com'], ['Cc', 'sam@example.com'], ['Subject', 'Thursday plan']])
    expect(draftRows(REPLY)).toEqual([['To', 'pat@example.com'], ['Subject', 'Re: Lunch'],
      ['In reply to', '“Lunch” from Pat <pat@example.com>']])
    // The email sync may not hold the message a reply answers.
    const unknown = {...REPLY, draft: {...REPLY.draft, replyTo: {from: null, subject: null}}}
    expect(draftRows(unknown).at(-1)).toEqual(['In reply to', 'A message in your Gmail'])
    const untitled = {...DRAFT, draft: {...DRAFT.draft, subject: ''}}
    expect(draftTitle(untitled)).toBe('A Gmail draft')
    expect(draftRows(untitled)).toContainEqual(['Subject', 'No subject'])
    expect(draftRows(UNREADABLE_DRAFT)).toEqual([])
  })

  it('offers to create only a draft shown whole that still waits', () => {
    expect(draftActions(DRAFT)).toEqual(['approve', 'decline'])
    expect(draftActions({...DRAFT, tooLarge: true, draft: null, sha256: null})).toEqual(['decline'])
    expect(draftActions(REPLY)).toEqual(['check', 'dismiss'])
    expect(draftActions(UNREADABLE_DRAFT)).toEqual(['decline'])
    expect(draftActions({...DRAFT, state: 'rejected'})).toEqual(['dismiss'])
  })

  it('says plainly what happened, and that Portal never sends', () => {
    expect(draftStateLine(DRAFT)).toBeNull()
    expect(draftStateLine({...DRAFT, tooLarge: true})).toMatch(/too long to show here/)
    expect(draftOutcomeNotice('applied', DRAFT)).toBe('Done. The draft is in your Gmail Drafts. Open Gmail to read it over and send it.')
    expect(draftOutcomeNotice('declined', DRAFT)).toBe('Declined. Nothing was written to Gmail.')
    expect(draftOutcomeNotice('declined', REPLY)).toBe('Dismissed.')
    expect(draftOutcomeNotice('unconfirmed', DRAFT)).toMatch(/ODS won't create it again/)
    expect(draftOutcomeNotice('rejected', DRAFT)).toBe('Gmail refused this draft. Ask Portal again if you still want it.')
    for (const state of ['invalid', 'unconfirmed', 'applied', 'rejected', 'not-applied']) {
      expect(draftStateLine({...DRAFT, state})).not.toMatch(/error|fail/i)
    }
  })
})
