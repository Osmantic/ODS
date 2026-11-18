// OpenClaw teaches webchat sessions, Portal among them, to write Control UI
// embed directives such as [embed ref="cv_123" title="Status" height="320" /]
// or [embed url="/__openclaw__/canvas/..." /]. Portal displays none of them:
// conversation text never becomes a URL, link, iframe, download or file
// selection. A directive line is left out of the shown reply, without a note.
// Seen live: a dragon picture's reply ended with [embed ref="site-..." /] for
// the very site its preview card was showing, so a note saying Portal can't
// display it read as an error and was false. A delivered file's own directive
// is resolved before this by deliveredArtifactDisplayText. Presentation only:
// stored chat, model history and the copy button keep the reply's original text.
export const EMBED_ONLY_NOTE = 'This reply tried to show an embedded view, which Portal can’t display. Ask again for a plain-text answer.'
export const EMBED_NOTES = [EMBED_ONLY_NOTE]

const VALUE = String.raw`(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s"'\]]+)`
// A line holding only one directive, in any attribute order or quoting, with or
// without the closing slash. Four or more leading spaces make it code: kept.
// Every whitespace run has one owner, so matching stays linear on long lines.
const DIRECTIVE = new RegExp(String.raw`^ {0,3}\[embed(?:\s+[\w:.-]+\s*=\s*${VALUE})+\s*(?:\/\s*)?\]\s*$`, 'i')
// The unfinished last line of a streaming reply that may still become one.
const UNFINISHED = /^ {0,3}\[(?:e(?:m(?:b(?:e(?:d(?:\s[^\]\r\n]*)?)?)?)?)?)?$/i
const FENCE = /^ {0,3}(`{3,}|~{3,})([^\n]*)$/
// The workspace preview a directive names, as ref="site-<24 hex>".
const SITE_REF = /\sref\s*=\s*["']?(site-[a-f0-9]{24})(?![\w-])/i

// Leaves out each stand-alone directive line outside fenced code; an empty line
// in its place keeps the paragraphs around it apart. A reply of nothing but
// directives shows nothing when its message shows something else (`shown`: a
// preview card or delivered files) or each names a preview the chat shows
// (`shownSite`), and one note otherwise. While streaming, such a reply shows
// nothing yet and an unfinished last line is held back, so the shown text only
// ever grows.
export function embedDisplayText(content, { streaming = false, shown = false, shownSite = () => false } = {}) {
  if (typeof content !== 'string' || !/^ {0,3}\[/m.test(content)) return content
  const lines = content.split('\n')
  const kept = [], sites = []
  let fence = null, changed = false, other = false
  for (const [index, line] of lines.entries()) {
    const text = line.endsWith('\r') ? line.slice(0, -1) : line
    const marker = FENCE.exec(text)
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null
    } else if (marker && !(marker[1][0] === '`' && marker[2].includes('`'))) {
      fence = marker[1]
    } else if (DIRECTIVE.test(text)) {
      kept.push('')
      sites.push(SITE_REF.exec(text)?.[1])
      changed = true
      continue
    } else if (streaming && index === lines.length - 1 && UNFINISHED.test(text)) {
      kept.push('')
      changed = true
      continue
    }
    kept.push(line)
    other ||= text.trim() !== ''
  }
  if (!changed) return content
  if (!other) return streaming || shown || sites.every(site => site && shownSite(site)) ? '' : EMBED_ONLY_NOTE
  return kept.join('\n')
}
