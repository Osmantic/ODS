import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { fileLanguage, PixelCodeLines, PixelLanguageBadge } from './PixelCodeBlock'
import './pixel-file-changes.css'

const LABELS = {created:'Created', modified:'Edited', deleted:'Deleted', published:'Published'}
const count = value => Number.isSafeInteger(value) && value >= 0

export function PixelChangeCounts({additions, deletions}) {
  if (!count(additions) || !count(deletions)) return null
  return <span className="artifact-line-counts" aria-label={`${additions} lines added, ${deletions} lines removed`}><span className="change-positive">+{additions}</span><span className="change-negative">-{deletions}</span></span>
}

function ContextFold({children, length}) {
  const [expanded, setExpanded] = useState(false)
  return <span className="diff-context-fold"><button type="button" className="diff-context-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{length} unchanged lines{expanded ? ' · Collapse' : ''}</button>{expanded && children}</span>
}

function DiffLines({rows, path}) {
  const folds = new Map()
  for (let start = 0; start < rows.length;) {
    if (rows[start].type !== 'context') { start++; continue }
    let end = start + 1
    while (end < rows.length && rows[end].type === 'context' && rows[end].oldLine === rows[end-1].oldLine + 1 && rows[end].newLine === rows[end-1].newLine + 1) end++
    if (end - start > 6) {
      const from = start === 0 ? start : start + 3
      const to = end === rows.length ? end : end - 3
      if (to > from) folds.set(from, to)
    }
    start = end
  }
  const rendered = []
  return <PixelCodeLines source={`${rows.map(row => row.text).join('\n')}\n`} language={fileLanguage(path)} renderLine={(content, index) => {
    const row = rows[index]
    if (!row) return null
    const previous = rows[index - 1]
    // Supplied hunks may omit unknown context. Never call those gaps unchanged.
    const gap = previous && ((row.oldLine !== null && previous.oldLine !== null && row.oldLine > previous.oldLine + 1) || (row.newLine !== null && previous.newLine !== null && row.newLine > previous.newLine + 1))
    const line = <span key={index} className={`artifact-diff-line ${row.type === 'add' ? 'added' : row.type === 'remove' ? 'removed' : 'context'}`} data-line={row.type === 'remove' ? row.oldLine : row.newLine}><span className="code-line-content">{content}{row.noFinalNewline && <span className="diff-eof-marker" title="No newline at end of file"> ↵̸</span>}{index < rows.length - 1 ? '\n' : ''}</span></span>
    rendered[index] = line
    const fold = [...folds].find(([start,end]) => index >= start && index < end)
    if (fold && index < fold[1] - 1) return null
    if (fold) return <ContextFold key={`fold-${fold[0]}`} length={fold[1] - fold[0]}>{rendered.slice(fold[0], fold[1])}</ContextFold>
    return gap ? <span key={`gap-${index}`}><span className="diff-omitted" aria-label="Omitted lines">…</span>{line}</span> : line
  }}/>
}

function FileChange({file, onPreview}) {
  const [open, setOpen] = useState(false)
  const [copyState, setCopyState] = useState('Copy')
  const rows = Array.isArray(file.diff) ? file.diff : []
  const hasCounts = count(file.additions) && count(file.deletions)
  const validRows = rows.every(row => row && ['context','add','remove'].includes(row.type) && typeof row.text === 'string' && !row.text.includes('\n') &&
    (row.oldLine === null || Number.isSafeInteger(row.oldLine) && row.oldLine > 0) &&
    (row.newLine === null || Number.isSafeInteger(row.newLine) && row.newLine > 0))
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(rows.map(row => `${row.type === 'add' ? '+' : row.type === 'remove' ? '-' : ' '}${row.text}${row.noFinalNewline ? '\n\\ No newline at end of file' : ''}`).join('\n'))
      setCopyState('Copied')
    } catch { setCopyState('Copy failed') }
  }
  return <details className="chat-file-change" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><Pencil size={16} strokeWidth={1.25} aria-hidden="true"/><span className="file-change-name">{LABELS[file.change] || 'Changed'} {file.path}</span><PixelChangeCounts additions={file.additions} deletions={file.deletions}/></summary>
    {open && <div className="inline-artifact-content">
      {file.change === 'published' && <p className="pixel-publication-baseline">First published version. No earlier snapshot was available for comparison.</p>}
      {!validRows || !hasCounts && (file.additions !== null || file.deletions !== null) ? <p role="status">Changes could not be verified.</p> : !hasCounts ? <p role="status">Line comparison unavailable for this file.</p> : !rows.length ? <p role="status">{file.additions || file.deletions ? 'Line changes are unavailable for this file.' : 'No line changes.'}</p> : <section className="pixel-code-block artifact-diff" aria-label={`Changes to ${file.path}`}>
        <header className="code-block-header"><PixelLanguageBadge path={file.path}/><span title={file.path}>{file.path}</span><PixelChangeCounts additions={file.additions} deletions={file.deletions}/>{onPreview && file.change !== 'deleted' && <button type="button" onClick={() => onPreview(file)} aria-label={`Preview ${file.path}`}>Preview</button>}<button type="button" onClick={copy} aria-label={`Copy changes to ${file.path}`}>{copyState}</button></header>
        <pre tabIndex={0} aria-label={`Diff for ${file.path}`}><DiffLines rows={rows} path={file.path}/></pre>
        {file.truncated && <p className="pixel-diff-notice" role="status">Only part of this diff is displayed. Counts cover the verified file change.</p>}
        {copyState === 'Copy failed' && <p className="pixel-diff-notice" role="alert">Clipboard access failed. Select the changes to copy them manually.</p>}
      </section>}
    </div>}
  </details>
}

/** Receives verified changes only. Does not derive counts from truncated rows. */
export default function PixelFileChanges({changes = [], onPreview}) {
  if (!changes.length) return null
  return <div className="pixel-file-changes" aria-label="File changes">{changes.map(file => <FileChange key={file.path} file={file} onPreview={onPreview}/>)}</div>
}
