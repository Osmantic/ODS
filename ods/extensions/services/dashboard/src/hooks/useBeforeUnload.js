import { useEffect } from 'react'

/** Request the browser's leave-page confirmation only while edits may be lost. */
export function useBeforeUnload(enabled) {
  useEffect(() => {
    if (!enabled) return
    const protectDraft = (event) => {
      event.preventDefault()
      event.returnValue = true // Legacy browsers also require returnValue.
    }
    window.addEventListener('beforeunload', protectDraft)
    return () => window.removeEventListener('beforeunload', protectDraft)
  }, [enabled])
}
