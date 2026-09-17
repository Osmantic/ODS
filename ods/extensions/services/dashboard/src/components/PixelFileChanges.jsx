import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy, FileText, PanelRightClose, PanelRightOpen } from 'lucide-react'
import PortalFileTree from './PortalFileTree'
import PortalFileTreeResize,{useFileTreeResize} from './PortalFileTreeResize'
import { fileLanguage, PixelCodeLines, PixelLanguageBadge } from './PixelCodeBlock'
import './pixel-file-changes.css'

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
  let lastOldLine = null, lastNewLine = null
  return <PixelCodeLines source={`${rows.map(row => row.text).join('\n')}\n`} language={fileLanguage(path)} renderLine={(content, index) => {
    const row = rows[index]
    if (!row) return null
    // Additions have no old coordinate and removals have no new coordinate.
    // Keep each side's last position across those rows so omitted context stays visible.
    const gap = (row.oldLine !== null && lastOldLine !== null && row.oldLine > lastOldLine + 1)
      || (row.newLine !== null && lastNewLine !== null && row.newLine > lastNewLine + 1)
    if (row.oldLine !== null) lastOldLine = row.oldLine
    if (row.newLine !== null) lastNewLine = row.newLine
    const line = <span key={index} className={`artifact-diff-line ${row.type === 'add' ? 'added' : row.type === 'remove' ? 'removed' : 'context'}`} data-line={row.type === 'remove' ? row.oldLine : row.newLine}><span className="code-line-content">{content}{row.noFinalNewline && <span className="diff-eof-marker" title="No newline at end of file"> ↵̸</span>}{index < rows.length - 1 ? '\n' : ''}</span></span>
    rendered[index] = line
    const fold = [...folds].find(([start,end]) => index >= start && index < end)
    if (fold && index < fold[1] - 1) return null
    if (fold) return <ContextFold key={`fold-${fold[0]}`} length={fold[1] - fold[0]}>{rendered.slice(fold[0], fold[1])}</ContextFold>
    return gap ? <span key={`gap-${index}`}><span className="diff-omitted" aria-label="Omitted lines">…</span>{line}</span> : line
  }}/>
}

function FileChange({file, onPreview, onOpenFile, treeOpen, onToggleTree}) {
  const [copyState, setCopyState] = useState('Copy')
  const rows = Array.isArray(file.diff) ? file.diff : []
  const copyRequest = useRef(null)
  // Receipts belong to the displayed patch, not merely its filename.
  const patch = rows.map(row => `${row?.type === 'add' ? '+' : row?.type === 'remove' ? '-' : ' '}${row?.text}${row?.noFinalNewline ? '\n\\ No newline at end of file' : ''}`).join('\n')
  useEffect(() => {
    setCopyState('Copy')
    return () => { clearTimeout(copyRequest.current?.timer); copyRequest.current = null }
  }, [patch])
  const hasCounts = count(file.additions) && count(file.deletions)
  const positiveLine = value => Number.isSafeInteger(value) && value > 0
  const validRows = Array.isArray(file.diff) && rows.every(row => row && ['context','add','remove'].includes(row.type) && typeof row.text === 'string' && !/[\r\n\0]/.test(row.text) &&
    (row.type === 'add' ? row.oldLine === null && positiveLine(row.newLine) : row.type === 'remove' ? row.newLine === null && positiveLine(row.oldLine) : positiveLine(row.oldLine) && positiveLine(row.newLine)))
  const unverifiable = !validRows || !hasCounts && (file.additions !== null || file.deletions !== null)
  const canCopy = !unverifiable && hasCounts && rows.length > 0
  const copy = async () => {
    if (copyRequest.current || !canCopy) return
    const request = {timer:null}
    copyRequest.current = request
    setCopyState('Copying…')
    try {
      await Promise.race([
        navigator.clipboard.writeText(patch),
        new Promise((_, reject) => { request.timer = setTimeout(() => reject(new Error('Clipboard timed out')), 5000) }),
      ])
      if (copyRequest.current === request) setCopyState('Copied')
    } catch { if (copyRequest.current === request) setCopyState('Copy failed') }
    finally {
      clearTimeout(request.timer)
      if (copyRequest.current === request) copyRequest.current = null
    }
  }
  const openFile = onOpenFile || onPreview
  const TreeIcon = treeOpen ? PanelRightClose : PanelRightOpen
  return <section className="portal-review-diff pixel-code-block artifact-diff" aria-label={`Changes to ${file.path}`}>
    <header className="code-block-header portal-review-file-header">
      <PixelLanguageBadge path={file.path}/><span className="portal-review-path" title={file.path}>{file.path}</span><PixelChangeCounts additions={file.additions} deletions={file.deletions}/>
      {file.change === 'deleted' && <span className="portal-review-deleted">Deleted</span>}
      {openFile && file.change !== 'deleted' && <button type="button" onClick={() => openFile(file)} title="Open file" aria-label={`Open file ${file.path}`}><FileText size={14} aria-hidden="true"/></button>}
      {canCopy && <button type="button" onClick={copy} disabled={copyState === 'Copying…'} title={copyState} aria-label={`Copy changes to ${file.path}`}>{copyState === 'Copied' ? <Check size={14} aria-hidden="true"/> : <Copy size={14} aria-hidden="true"/>}</button>}
      <button type="button" className="portal-review-tree-toggle" title={treeOpen ? 'Hide files' : 'Show files'} aria-label="Toggle changed files" aria-expanded={treeOpen} onClick={onToggleTree}><TreeIcon size={15} aria-hidden="true"/></button>
    </header>
    {unverifiable ? <p className="portal-review-empty" role="status">Changes could not be verified.</p> : !hasCounts ? <p className="portal-review-empty" role="status">Line comparison unavailable for this file.</p> : !rows.length ? <p className="portal-review-empty" role="status">{file.additions || file.deletions ? 'Line changes are unavailable for this file.' : 'No line changes.'}</p> : <pre tabIndex={0} aria-label={`Diff for ${file.path}`}><DiffLines rows={rows} path={file.path}/></pre>}
    {file.truncated && !unverifiable && <p className="pixel-diff-notice" role="status">Only part of this diff is displayed. Counts cover the verified file change.</p>}
    {copyState === 'Copy failed' ? <p className="pixel-diff-notice" role="alert">Clipboard access failed. Select the changes to copy them manually.</p> : copyState === 'Copied' && <span className="sr-only" role="status">Changes copied.</span>}
  </section>
}

/** Receives verified changes only. Does not derive counts from truncated rows. */
export default function PixelFileChanges({changes = [], files, comparisonReady=true, rootPath, onPreview, selectedPath, onSelectFile, onOpenFile}) {
  const [localPath, setLocalPath] = useState(null)
  const [treePreference, setTreePreference] = useState(null)
  const panel = useRef(null), openedUnchanged = useRef(null)
  const hasManifest = Array.isArray(files)
  const treeFiles = useMemo(() => {
    if (!Array.isArray(files)) return changes
    const byPath = new Map(changes.map(file => [file.path,file]))
    // Only the current verified manifest supplies source files. Removed paths
    // are review-only entries and never inherit an old snapshot's file hash.
    return [...files.map(file => ({...file,...byPath.get(file.path)})),...changes.filter(file => file.change === 'deleted')]
  }, [files,changes])
  const treeLayout=useFileTreeResize(panel,{enabled:treeFiles.length>0})
  const {narrow}=treeLayout
  const path = selectedPath ?? localPath
  const missingSelection = hasManifest && comparisonReady && path && !treeFiles.some(file=>file.path===path)
  const selected = missingSelection ? null : changes.find(file => file.path === path) || changes[0]
  const requestedFile = hasManifest && files.find(file => file.path === selectedPath)
  useEffect(() => {
    if (!comparisonReady || !requestedFile || changes.some(file => file.path === requestedFile.path) || !onOpenFile) return
    const key = `${requestedFile.path}/${requestedFile.sha256}`
    if (openedUnchanged.current === key) return
    openedUnchanged.current = key
    onOpenFile(requestedFile)
  }, [comparisonReady,requestedFile,changes,onOpenFile])
  if (!treeFiles.length) return null
  const treeOpen = treePreference ?? !narrow
  const selectFile = (path, file) => {
    if (hasManifest && !file.change) { onOpenFile?.(file); return }
    setLocalPath(path)
    onSelectFile?.(path,file)
    if (narrow) setTreePreference(false)
  }
  return <div ref={panel} className={`pixel-file-changes portal-review-layout${treeOpen ? ' has-files' : ''}${narrow ? ' is-narrow' : ' portal-tree-split'}`} style={treeLayout.style} aria-label="File changes">
    {selected ? <FileChange key={selected.path} file={selected} onPreview={onPreview} onOpenFile={onOpenFile} treeOpen={treeOpen} onToggleTree={() => setTreePreference(!treeOpen)}/> : <section className="portal-review-diff pixel-code-block" aria-label="Project files">
      <header className="code-block-header portal-review-file-header"><span>{missingSelection ? 'File unavailable' : comparisonReady ? 'No changes' : 'Project files'}</span><button type="button" className="portal-review-tree-toggle" title={treeOpen ? 'Hide files' : 'Show files'} aria-label="Toggle changed files" aria-expanded={treeOpen} onClick={() => setTreePreference(!treeOpen)}>{treeOpen ? <PanelRightClose size={15}/> : <PanelRightOpen size={15}/>}</button></header>
      <p className="portal-review-empty" role={missingSelection ? 'status' : undefined}>{missingSelection ? 'This file is not part of the current project. Select another file.' : 'Select a file to view its contents.'}</p>
    </section>}
    {treeOpen && <PortalFileTreeResize layout={treeLayout} label="Resize changed file list"/>}
    {treeOpen && <aside className="portal-review-files"><PortalFileTree files={treeFiles} rootPath={rootPath} selectedPath={selected?.path} onSelectFile={selectFile} label={hasManifest ? 'Project files' : 'Changed files'} filterLabel={hasManifest ? 'Filter project files' : 'Filter changed files'}/></aside>}
  </div>
}
