import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useBlocker } from 'react-router-dom'

const path = location => location.pathname.replace(/\/+$/, '') || '/'

export default function UnsavedIntegrationsNavigation({ dirty }) {
  // Settings sections stay mounted across query changes. A pathname change
  // removes the editor, whether it came from a Link, navigate() or browser POP.
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && path(currentLocation) !== path(nextLocation))
  const dialog = useRef(null), returnFocus = useRef(null)
  const title = useId()

  useEffect(() => {
    if (blocker.state === 'blocked' && !dirty) { blocker.reset(); return }
    if (blocker.state === 'blocked') {
      if (!dialog.current.open) {
        returnFocus.current = document.activeElement
        dialog.current.showModal()
      }
    } else if (dialog.current.open) {
      dialog.current.close()
      if (blocker.state !== 'proceeding' && returnFocus.current?.isConnected) returnFocus.current.focus()
    }
  }, [blocker, dirty])

  // The editor may be hidden on another Settings tab. Keep its confirmation
  // outside that hidden subtree so a blocked navigation is always visible.
  return createPortal(<dialog ref={dialog} aria-labelledby={title}
    className="max-w-md rounded-xl border border-theme-border bg-theme-card p-5 text-theme-text shadow-xl backdrop:bg-black/50"
    onCancel={event => { event.preventDefault(); if (blocker.state === 'blocked') blocker.reset() }}>
    <h2 id={title} className="text-lg font-semibold">Leave Portal integrations?</h2>
    <p className="mt-2 text-sm text-theme-text-muted">Unsaved settings and typed credentials will be discarded. Changes already saved are kept.</p>
    <div className="mt-5 flex flex-wrap justify-end gap-2">
      <button type="button" autoFocus className="rounded border border-theme-border px-3 py-2 text-sm focus:ring-2 focus:ring-theme-accent"
        onClick={() => { if (blocker.state === 'blocked') blocker.reset() }}>Stay here</button>
      <button type="button" className="rounded bg-theme-accent px-3 py-2 text-sm text-theme-bg focus:ring-2 focus:ring-theme-accent"
        onClick={() => { if (blocker.state === 'blocked') blocker.proceed() }}>Leave without saving</button>
    </div>
  </dialog>, document.body)
}
