import { EMBED_NOTE, EMBED_ONLY_NOTE, embedDisplayText } from './embedDisplay'
import { deliveredArtifactDisplayText, parseDeliveredArtifacts } from './pixelDeliveredArtifacts'

// The whole reply a fleet test showed verbatim (Qwen3.5-4B, 2026-10-18).
const FLEET = '[embed ref="cv_456e4732d60d4117b731214838589d22" height="320"]'
// The hosted canvas form OpenClaw's webchat prompt teaches.
const CANVAS = '[embed url="/__openclaw__/canvas/documents/cv_123/index.html" title="Status" height="320" /]'
const paragraphs = text => text.split(/\n\s*\n/).map(part => part.trim()).filter(Boolean)

it('shows a note instead of a reply that is only an embed Portal cannot display', () => {
  expect(embedDisplayText(FLEET)).toBe(EMBED_ONLY_NOTE)
  expect(embedDisplayText(`${FLEET}\n`)).toBe(EMBED_ONLY_NOTE)
  expect(embedDisplayText(`\n${FLEET}\n\n${CANVAS}\r\n`)).toBe(EMBED_ONLY_NOTE)
})

it('keeps the prose around an embed and leaves a note in its place', () => {
  expect(embedDisplayText(`Here is the result:\n${FLEET}\nThe image shows a red circle.`))
    .toBe(`Here is the result:\n\n${EMBED_NOTE}\n\nThe image shows a red circle.`)
  const shown = embedDisplayText(`Status:\n\n${CANVAS}\n\nDone.\n\n${FLEET}`)
  expect(paragraphs(shown)).toEqual(['Status:', EMBED_NOTE, 'Done.', EMBED_NOTE])
  expect(shown).not.toContain('[embed')
})

it.each([
  CANVAS,
  '[embed ref="cv_123" title="Status" height="320" /]',
  '[embed height="320" ref="cv_123"]',
  "[embed ref='cv_123' height='320' /]",
  '[embed ref=cv_123 height=320]',
  '[embed  ref = "cv_123"  title="Status [beta]"/]',
  '[EMBED REF="cv_123" /]',
  '   [embed ref="cv_123" /]  ',
  '[embed url="https://example.com/chart" /]',
  `[embed ref="artifact_${'c'.repeat(64)}" title="report.md" height="480" /]`,
])('treats %s as an embed Portal cannot display', line => {
  expect(embedDisplayText(line)).toBe(EMBED_ONLY_NOTE)
  expect(paragraphs(embedDisplayText(`Before\r\n${line}\r\nAfter`))).toEqual(['Before', EMBED_NOTE, 'After'])
})

it('keeps a list item\'s embed note inside the item', () => {
  expect(embedDisplayText(`1. Status\n   ${FLEET}\n2. Next`)).toBe(`1. Status\n\n   ${EMBED_NOTE}\n\n2. Next`)
})

it('leaves embed syntax in code untouched', () => {
  for (const content of [
    '```\n' + FLEET + '\n```',
    '~~~md\n' + CANVAS + '\n~~~',
    'Write it like this:\n\n```text\n' + FLEET + '\n```\n\nThen send it.',
    '````\n```\n' + FLEET + '\n```\n````',
    '```\n' + FLEET,
    'Use `' + FLEET + '` in OpenClaw.',
    '`' + FLEET + '`',
    '    ' + FLEET,
  ]) {
    expect(embedDisplayText(content)).toBe(content)
    expect(embedDisplayText(content, { streaming: true })).toBe(content)
  }
  expect(paragraphs(embedDisplayText('```\ncode\n```\n' + FLEET))).toEqual(['```\ncode\n```', EMBED_NOTE])
})

it('leaves other bracketed text and inline mentions as written', () => {
  for (const content of [
    '[OpenClaw](https://openclaw.ai) has docs.',
    '[1] A source',
    '[embed]',
    '[embedded systems] are everywhere',
    `${FLEET} is the syntax OpenClaw uses.`,
    `> ${FLEET}`,
    `- ${FLEET}`,
    'No brackets at all.',
  ]) expect(embedDisplayText(content)).toBe(content)
  expect(embedDisplayText('')).toBe('')
  expect(embedDisplayText(null)).toBeNull()
  expect(embedDisplayText(undefined)).toBeUndefined()
})

it('leaves a delivered file\'s exact directive to its receipt check', () => {
  const sha = '8aee' + '0'.repeat(60)
  const directive = `[embed ref="artifact_${sha}" title="report.md" height="480" /]`
  const artifacts = parseDeliveredArtifacts([{
    schemaVersion: 1, kind: 'ods-pixel-workspace-artifact', relativePath: 'Playground/project/report.md',
    siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64), file: { path: 'report.md', bytes: 12, sha256: sha },
  }])
  expect(embedDisplayText(deliveredArtifactDisplayText(directive, artifacts))).toBe('')
  // Without a matching receipt it is one more embed Portal cannot display.
  expect(embedDisplayText(deliveredArtifactDisplayText(directive, []))).toBe(EMBED_ONLY_NOTE)
  // The receipt arrives with the end of the stream: show nothing until then.
  expect(embedDisplayText(directive, { streaming: true })).toBe('')
})

it('holds back only an unfinished last line that may still become an embed', () => {
  expect(embedDisplayText('Status:\n[embed ref="cv_1', { streaming: true })).toBe('Status:\n')
  expect(embedDisplayText('Status:\n[', { streaming: true })).toBe('Status:\n')
  expect(embedDisplayText('Status:\n[1', { streaming: true })).toBe('Status:\n[1')
  expect(embedDisplayText('Status:\n[embed ref="cv_1"\nmore', { streaming: true })).toBe('Status:\n[embed ref="cv_1"\nmore')
  // A finished or stopped reply shows what it has.
  expect(embedDisplayText('Status:\n[embed ref="cv_1')).toBe('Status:\n[embed ref="cv_1')
})

it.each([
  `Here is the result:\n\n${FLEET}\n\nThe image shows a red circle.`,
  `${FLEET}\nThe image shows a red circle.`,
  `${CANVAS}\n${FLEET}`,
  `Two views:\n${CANVAS}\n${FLEET}\nThat is all.`,
])('never shows directive text while %j streams, and the shown text only grows', reply => {
  let previous = ''
  for (let end = 1; end <= reply.length; end++) {
    const shown = embedDisplayText(reply.slice(0, end), { streaming: true })
    expect(shown).not.toContain('[')
    expect(shown.startsWith(previous)).toBe(true)
    previous = shown
  }
  expect(embedDisplayText(reply).startsWith(previous)).toBe(true)
})

it('stays linear on long lines that only look like an embed or a fence', () => {
  // Quadratic backtracking would take minutes on these, far past the test timeout.
  const spaces = ' '.repeat(200000)
  for (const content of [`[embed a=b${spaces}x]`, `[embed a=x/${spaces}y]`, '`'.repeat(200000) + '\u2028\n' + FLEET])
    expect(embedDisplayText(content)).toBe(content)
  expect(embedDisplayText(`[embed a=b${spaces}/]`)).toBe(EMBED_ONLY_NOTE)
})
