import {describe, expect, it} from 'vitest'
import {publicationDisplayText} from './publicationDisplay'

const digest = 'b'.repeat(64)
const siteId = `site-${digest.slice(0, 24)}`
const publication = {
  schemaVersion:1, kind:'ods-pixel-workspace-preview', siteId, sha256:digest,
  port:3005, url:`http://${siteId}.localhost:3005/${siteId}/`,
}
const scope = 'Publication scope: this receipt verifies the published snapshot, not functional behavior or completion of other requested work.'
const link = `[Open preview](${publication.url})`
const receipt = `Your preview is ready.\n\n${link}\n\nPublished from your workspace.\n${scope}`

describe('publication display text', () => {
  it('uses the publication card in place of the exact generated receipt, retaining the original data', () => {
    const message = Object.freeze({content:`Updated keyboard controls. Mobile controls still need work.\n\n${receipt}`, publication:Object.freeze(publication)})
    expect(publicationDisplayText(message.content, message.publication)).toBe('Updated keyboard controls. Mobile controls still need work.')
    expect(message.content).toContain(scope)
    expect(publicationDisplayText(receipt, publication)).toBe('')
  })

  it('removes duplicate trailing receipts including the authored variant and CRLF delivery', () => {
    const authored = receipt.replace('Published from your workspace.', 'Created by Pixel.')
    const current = receipt.replace('Published from your workspace.', 'Created by Portal.')
    expect(publicationDisplayText(`Changes documented.\n\n${authored}\n\n${receipt}`.replaceAll('\n', '\r\n'), publication)).toBe('Changes documented.')
    expect(publicationDisplayText(`${link}\n\nCreated by Pixel.`, publication)).toBe('')
    expect(publicationDisplayText(current, publication)).toBe('')
  })

  it('accepts the same publication relay and current origin relay, while preserving unrelated links', () => {
    for (const url of [`/pixel-preview/${siteId}/`, `${window.location.origin}/pixel-preview/${siteId}/`]) {
      expect(publicationDisplayText(receipt.replace(publication.url,url), publication)).toBe('')
    }
    for (const url of ['https://example.com/demo/', `https://unrelated.example/pixel-preview/${siteId}/`, `${publication.url}?version=2`, publication.url.replace('site-b','site-c')]) {
      const text = receipt.replace(publication.url,url)
      expect(publicationDisplayText(text, publication)).toBe(text)
    }
  })

  it('does not infer a publication from text or incomplete, invalid metadata', () => {
    for (const candidate of [null, undefined, {}, {...publication,schemaVersion:2}, {...publication,siteId:'site-bad'}, {...publication,sha256:'c'.repeat(64)}, {...publication,url:'https://example.com/'}, {...publication,port:0}]) {
      expect(publicationDisplayText(receipt, candidate)).toBe(receipt)
    }
  })

  it('preserves stale snapshot warnings, failed work, and nonpublication receipt scope', () => {
    const stale = `Your last published preview is still available.\n\n[Open last published preview](${publication.url})\n\nThe workspace has not been verified again since later tool activity. This snapshot may not include subsequent changes; publish again to verify the current files.`
    expect(publicationDisplayText(stale, publication)).toBe(stale)
    const failure = 'Browser verification failed. The game is still broken.'
    expect(publicationDisplayText(`${failure}\n\n${receipt}`, publication)).toBe(failure)
    const operations = `${link}\n\nReceipt scope: the Operations evidence above does not establish completion of other requested work.`
    expect(publicationDisplayText(operations, publication)).toBe(operations)
  })

  it('leaves quoted examples, code fences, indentation, and additional useful prose untouched', () => {
    for (const text of [`Example:\n\n\`\`\`markdown\n${receipt}`, `Example:\n\n~~~markdown\n${receipt}`, receipt.split('\n').map(line=>`> ${line}`).join('\n'), receipt.split('\n').map(line=>`    ${line}`).join('\n'), `${receipt}\n\nThe controls support WASD and arrow keys.`, `[Open preview](${publication.url}) to play the game.`, 'Created by Pixel.']) {
      expect(publicationDisplayText(text, publication)).toBe(text)
    }
  })

  it('allows a real receipt after a closed code example without altering the example', () => {
    const example = `Example:\n\n\`\`\`markdown\n${receipt}\n\`\`\``
    expect(publicationDisplayText(`${example}\n\n${receipt}`, publication)).toBe(example)
  })
})

it('keeps host-authored storage scope while removing the preview receipt', () => {
  const storage='Inputs in the embedded preview may be lost on reload. Storage behavior can differ in a separate tab; publishing alone does not guarantee saved data.'
  const model='For actual persistence, open it in your regular browser.'
  expect(publicationDisplayText(`${model}\n\n${storage}\n\n${receipt}`, publication)).toBe(`${model}\n\n${storage}`)
})

// A failed preview check keeps the agent's own explanation ahead of the host
// note (pixel-agent preview-explanation.mjs); only the receipt becomes the card.
it('keeps a kept agent explanation and the failed-check note while removing the receipt', () => {
  const failedReceipt = `Your preview is ready.\n\n${link}\n\nPublished from your workspace.`
  const note = "**Portal check:** The reply above is the agent's own account, not a verified result. " +
    "This request did not pass Portal's checks.\n\n" +
    'Latest browser inspection of this preview: step 2 (assert-painted) on `#arena` captured one color only (#8fce3a).\n\n' +
    'The attempted preview interaction checks have not passed browser inspection. ' +
    'The published preview is available, but those checks remain unverified.'
  const explanation = 'The cause is `ui.js:748`: a canvas is not a valid `fillStyle`.\n\n- The last full-viewport fill covers the arena.'
  expect(publicationDisplayText(`${explanation}\n\n${note}\n\n${failedReceipt}`, publication)).toBe(`${explanation}\n\n${note}`)
  // The plugin closes a code block the agent left open with that block's own
  // fence (last line of each sample), so this reader sees the receipt outside it.
  const code = 'g.fillRect(0, 0, W, H);'
  for (const fenced of [
    `The last draw call:\n\n${'`'.repeat(3)}js\n${code}\n${'`'.repeat(3)}`,
    `Nested sample:\n\n${'`'.repeat(4)}markdown\n${'`'.repeat(3)}js\n${code}\n${'`'.repeat(3)}\n${'`'.repeat(4)}`,
    `Mixed fences:\n\n${'`'.repeat(3)}markdown\n~~~\n${code}\n${'`'.repeat(3)}`,
    `Tilde block:\n\n~~~~\n${code}\n~~~\n~~~~`,
  ]) expect(publicationDisplayText(`${fenced}\n\n${note}\n\n${failedReceipt}`, publication)).toBe(`${fenced}\n\n${note}`)
})
