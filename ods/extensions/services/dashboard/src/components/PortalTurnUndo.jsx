import {useEffect, useRef, useState} from 'react'
import HelpLink from './HelpLink'
import {changedProjects, restoreProject} from '../lib/projectRestore'

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`
// The listed names, then how many more.
function named(items, count) {
  const names = count > items.length ? [...items, `${count - items.length} more`] : items
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0]
}

// What a finished restore did, what it left as it was and why, and what it
// could not change.
function outcomeText(receipt, undoing) {
  const {project, written, removed, kept, keptCount, failed, failedCount, complete, undo} = receipt
  const before = undoing ? 'before you put the files back' : 'before this turn'
  const changes = [written && `${plural(written, 'file')} put back`, removed && `${plural(removed, 'new file')} removed`].filter(Boolean)
  return [undo ? `${project} is back to how it was ${before}${changes.length ? `: ${changes.join(' and ')}` : ''}.`
    : `${project} already matches how it was ${before}, so nothing changed.`,
  keptCount ? `Not copied, so left as they are: ${named(kept, keptCount)}.` : '',
  complete ? '' : 'This project has too many files to check them all, so some were left as they are.',
  failedCount ? `${named(failed, failedCount)} could not be put back. Close any program that is using ${failedCount === 1 ? 'that file' : 'those files'}, then try again.` : '',
  ].filter(Boolean).join(' ')
}

// A refusal: its cause and what to do next. Nothing changed.
function refusalText(code, project, undoing) {
  if (code === 'busy') return 'Portal is still working. Put the files back when it finishes.'
  if (code === 'missing') return undoing ? 'ODS no longer has the copy from before you put the files back, so nothing was changed.'
    : `ODS no longer has a copy of ${project} from before this turn (it keeps only recent ones), so nothing was changed. Ask Portal to fix the files instead.`
  if (code === 'damaged') return `The copy ODS kept of ${project} is damaged, so nothing was changed. Ask Portal to fix the files instead.`
  return `Portal could not confirm that the files were put back. Check ${project}, then try again.`
}

const BUTTON = 'rounded px-1 py-0.5 text-theme-text-secondary underline underline-offset-[3px] hover:text-theme-text disabled:cursor-not-allowed disabled:opacity-50'

// One project's "Put back files" under a turn. A restore offers Undo, which
// puts the files back as they were just before it.
function ProjectUndo({chatId, runId, project, busy, labelled}) {
  const [working, setWorking] = useState('')
  const [undo, setUndo] = useState(null)
  const [notice, setNotice] = useState(null)
  const pending = useRef(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function putBack(undoing) {
    if (pending.current) return
    const controller = pending.current = new AbortController()
    setWorking(undoing ? 'undo' : 'restore')
    try {
      const receipt = await restoreProject({chatId, project, snapshot: undoing ? undo : runId}, controller.signal)
      if (controller.signal.aborted) return
      const retry = receipt.failedCount ? (undoing ? 'undo' : 'restore') : ''
      // Undo reverses the first restore until it is used in full.
      setUndo(current => undoing && !retry ? null : current ?? receipt.undo)
      setNotice({text: outcomeText(receipt, undoing), alert: Boolean(retry), retry})
    } catch (error) {
      if (controller.signal.aborted) return
      const code = error?.code ?? 'unavailable'
      setNotice({text: refusalText(code, project, undoing), alert: code !== 'busy', retry: ''})
    } finally {
      if (pending.current === controller) pending.current = null
      if (!controller.signal.aborted) setWorking('')
    }
  }

  const waiting = busy ? 'Portal is still working in this chat' : undefined
  return <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] leading-5 text-theme-text-muted"
    role="group" aria-label={`Files in ${project}`}>
    <span role="status" className={notice && !notice.alert ? 'basis-full' : 'sr-only'}>{notice && !notice.alert ? notice.text : ''}</span>
    {notice?.alert && <span role="alert" className="basis-full">{notice.text} <HelpLink/></span>}
    {(!undo || notice?.retry === 'restore') && <button type="button" className={BUTTON} disabled={busy || Boolean(working)}
      aria-busy={working === 'restore'} title={waiting} onClick={() => void putBack(false)}>
      {working === 'restore' ? 'Putting files back…' : labelled ? `Put back ${project} from before this turn` : 'Put back files from before this turn'}
    </button>}
    {undo && <button type="button" className={BUTTON} disabled={busy || Boolean(working)} aria-busy={working === 'undo'}
      title={waiting} aria-label={`Undo putting back ${project}`} onClick={() => void putBack(true)}>
      {working === 'undo' ? 'Undoing…' : 'Undo'}
    </button>}
  </div>
}

// "Put back files" for each Playground project a turn changed.
export default function PortalTurnUndo({chatId, task, busy = false}) {
  const projects = changedProjects(task)
  if (!chatId || !projects.length) return null
  return projects.map(project => <ProjectUndo key={project} chatId={chatId} runId={task.runId} project={project}
    busy={busy} labelled={projects.length > 1}/>)
}
