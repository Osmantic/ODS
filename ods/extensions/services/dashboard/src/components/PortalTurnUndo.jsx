import {useEffect, useRef, useState} from 'react'
import HelpLink from './HelpLink'
import {restoreProject} from '../lib/projectRestore'

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`
// The listed names, then how many more.
function named(items, count) {
  const names = count > items.length ? [...items, `${count - items.length} more`] : items
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0]
}
const pick = (count, one, many) => (count === 1 ? one : many)

// Toward 'before' puts back the files from before the turn; toward 'after'
// undoes that.
const PLACE = {before: 'how it was before this turn', after: 'how it was before you put the files back'}
const PREVIEW = 'A preview of this project still shows the earlier files until Portal publishes it again.'
const lateText = project => `ODS made this copy after the turn had already changed some files in ${project}, so putting it back may not undo every change.`

// Why some files could not be changed, and what to do: a step that can work,
// or a way to get help when none can.
function failureText({failed, failedCount, failure}) {
  const files = named(failed, failedCount), it = pick(failedCount, 'it', 'them')
  if (failure === 'in-use') return {text: `${files} could not be changed because a program is using ${it}. Close it, then try again.`, retry: true}
  if (failure === 'changed') return {text: `${files} changed while ODS was working, so ${pick(failedCount, 'it was', 'they were')} left as ${pick(failedCount, 'it is', 'they are')}. Try again to finish.`, retry: true}
  if (failure === 'permission') return {text: `ODS is not allowed to change ${files}, so ${pick(failedCount, 'it was', 'they were')} left as ${pick(failedCount, 'it is', 'they are')}.`, help: true}
  return {text: `${files} could not be changed.`, retry: true, help: true}
}

// What a finished restore did, what it left as it was and why, and what it
// could not change.
function outcomeNotice(receipt, toward) {
  const {project, status, written, removed, left, leftCount, failedCount, late} = receipt
  const lines = [], changes = [written && `${plural(written, 'file')} written back`, removed && `${plural(removed, 'file')} removed`].filter(Boolean)
  let help = false, retry = false, alert = false
  if (status === 'restored') lines.push(`${project} is back to ${PLACE[toward]}${changes.length ? `: ${changes.join(' and ')}` : ''}.`)
  else if (!leftCount) lines.push(`${project} already matches ${PLACE[toward]}, so nothing changed.`)
  else lines.push(`Nothing in ${project} was changed.`)
  if (late && toward === 'before') lines.push(lateText(project))
  if (leftCount) {
    lines.push(`ODS left ${named(left, leftCount)} as ${pick(leftCount, 'it is', 'they are')}, because its copies could not hold ${pick(leftCount, 'it', 'them')} (a link, a very large file, or past a copy's limits).`)
    help = true
  }
  if (failedCount) {
    const failure = failureText(receipt)
    lines.push(failure.text)
    help ||= Boolean(failure.help)
    retry = Boolean(failure.retry)
    alert = true
  }
  if (status === 'restored' && written + removed > 0) lines.push(PREVIEW)
  return {text: lines.join(' '), alert, help, retry}
}

// A refusal: its cause and what to do next. Nothing changed. gone: the copy
// cannot be used any more, so its button goes away.
function refusalNotice(code, project, toward) {
  const copy = toward === 'before' ? `the copy of ${project} from before this turn` : 'the copy from before you put the files back'
  switch (code) {
    case 'project-restore-busy':
      return {text: 'Portal is still working on a reply or a command it started, in this chat or another. Nothing was changed; try again once it finishes.'}
    case 'project-snapshot-pruned':
      return {text: `ODS no longer keeps ${copy} (it keeps the latest few), so nothing was changed.`, gone: true}
    case 'project-snapshot-unavailable':
      return {text: `ODS did not keep ${copy}, so nothing was changed.`, gone: true}
    case 'project-snapshot-damaged':
      return {text: `${copy[0].toUpperCase()}${copy.slice(1)} is damaged, so nothing was changed.`, gone: true, help: true, alert: true}
    case 'project-folder-unusable':
      return {text: `${project} is not a folder ODS can use (it may be a link or a file), so nothing was changed.`, help: true, alert: true}
    case 'project-copies-unusable':
      return {text: 'ODS cannot use the folder where it keeps project copies (its owner or permissions changed), so nothing was changed.', gone: true, help: true, alert: true}
    case 'project-restore-unsupported':
      return {text: 'This ODS install cannot put project files back, so nothing was changed.', gone: true, help: true, alert: true}
    default:
      return {text: `Portal could not confirm whether the files were put back. Check ${project} before you try again.`, help: true, alert: true}
  }
}

const BUTTON = 'rounded px-1 py-0.5 text-theme-text-secondary underline underline-offset-[3px] hover:text-theme-text disabled:cursor-not-allowed disabled:opacity-50'

// One project's "Put back files" under the latest turn that changed it. A
// restore offers Undo, which puts the files back as they were just before
// it; an Undo offers Put back again. entry comes from the host, so both
// survive a reload.
function ProjectUndo({chatId, entry, busy, labelled, onChanged}) {
  const {project} = entry
  // Which version the project holds ('after': the turn's own) and the copy
  // the next step puts back: the host's view until this page acts.
  const [local, setLocal] = useState(null)
  const [working, setWorking] = useState(false)
  const [notice, setNotice] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [retry, setRetry] = useState(null)
  const [gone, setGone] = useState(false)
  const pending = useRef(null)
  useEffect(() => () => pending.current?.abort(), [])
  const side = local?.side ?? entry.restore?.side ?? 'after'
  const target = local?.target ?? entry.restore?.undo ?? entry.snapshot

  async function run(step, token) {
    if (pending.current) return
    const controller = pending.current = new AbortController()
    setWorking(true)
    setConfirm(null)
    try {
      const receipt = await restoreProject({chatId, project, snapshot: step.target, confirm: token}, controller.signal)
      if (controller.signal.aborted) return
      if (receipt.status === 'confirm') {
        setNotice(null)
        setConfirm({step, ...receipt.confirm})
        return
      }
      const shown = outcomeNotice(receipt, step.toward)
      setNotice(shown)
      setRetry(shown.retry ? step : null)
      if (receipt.status === 'restored') {
        setLocal({side: step.toward, target: receipt.undo})
        onChanged?.()
      }
    } catch (error) {
      if (controller.signal.aborted) return
      const shown = refusalNotice(error?.code, project, step.toward)
      setNotice(shown)
      setRetry(null)
      if (shown.gone) setGone(true)
    } finally {
      if (pending.current === controller) pending.current = null
      if (!controller.signal.aborted) setWorking(false)
    }
  }

  const toward = side === 'after' ? 'before' : 'after'
  const step = {target, toward}
  const waiting = busy ? 'Portal is still working' : undefined
  const disabled = busy || working
  const putBackLabel = labelled ? `Put back ${project} from before this turn` : 'Put back files from before this turn'
  const mainLabel = toward === 'before' ? putBackLabel : 'Undo'
  return <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] leading-5 text-theme-text-muted"
    role="group" aria-label={`Files in ${project}`}>
    <span role="status" className={notice && !notice.alert ? 'basis-full' : 'sr-only'}>
      {notice && !notice.alert ? notice.text : ''}{notice && !notice.alert && notice.help ? <> <HelpLink/></> : null}
    </span>
    {notice?.alert && <span role="alert" className="basis-full">{notice.text}{notice.help && <> <HelpLink/></>}</span>}
    {!notice && !local && entry.late && side === 'after' && <span className="basis-full">{lateText(project)}</span>}
    {!notice && !confirm && local === null && entry.restore?.side === 'before' && <span className="basis-full">The files from before this turn were put back.</span>}
    {confirm && <span role="alert" className="basis-full">
      {confirm.step.toward === 'before' ? 'Putting back the files from before this turn' : 'Undoing'} will {[
        confirm.overwriteCount && `overwrite ${named(confirm.overwrite, confirm.overwriteCount)}`,
        confirm.removeCount && `remove ${named(confirm.remove, confirm.removeCount)}`,
      ].filter(Boolean).join(' and ')}, which may hold changes made {confirm.step.toward === 'before' ? 'after this turn' : 'after you put the files back'}.
    </span>}
    {confirm && <>
      <button type="button" className={BUTTON} disabled={disabled} title={waiting} onClick={() => void run(confirm.step, confirm.token)}>
        {confirm.step.toward === 'before' ? 'Put back anyway' : 'Undo anyway'}
      </button>
      <button type="button" className={BUTTON} disabled={working} onClick={() => setConfirm(null)}>Cancel</button>
    </>}
    {!confirm && !gone && retry && <button type="button" className={BUTTON} disabled={disabled} title={waiting}
      onClick={() => void run(retry)}>Try again</button>}
    {!confirm && !gone && <button type="button" className={BUTTON} disabled={disabled} aria-busy={working} title={waiting}
      aria-label={toward === 'after' ? `Undo putting back ${project}` : undefined} onClick={() => void run(step)}>
      {working ? (toward === 'before' ? 'Putting files back…' : 'Undoing…') : mainLabel}
    </button>}
  </div>
}

// "Put back files" for each Playground project whose latest changing turn is
// this one, as the host lists them.
export default function PortalTurnUndo({chatId, entries = [], busy = false, onChanged}) {
  if (!chatId || !entries.length) return null
  return entries.map(entry => <ProjectUndo key={entry.project} chatId={chatId} entry={entry} busy={busy}
    labelled={entries.length > 1} onChanged={onChanged}/>)
}
