import { useCallback, useEffect, useRef, useState } from 'react'
import SplashMetalOrb from './SplashMetalOrb'

export default function SplashScreen({ onComplete, preview = false }) {
  const dialog = useRef(null)
  const [leaving, setLeaving] = useState(false)
  const completed = useRef(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete
  const exitTimer = useRef(null)
  const complete = useCallback(() => {
    if (completed.current) return
    completed.current = true
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      onCompleteRef.current?.()
      return
    }
    setLeaving(true)
    exitTimer.current = window.setTimeout(() => onCompleteRef.current?.(), 400)
  }, [])
  useEffect(() => {
    const node = dialog.current
    node.showModal()
    return () => node.close()
  }, [])
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      complete()
      return
    }
    const timer = preview ? null : window.setTimeout(complete, 3200)
    return () => { window.clearTimeout(timer); window.clearTimeout(exitTimer.current) }
  }, [complete, preview])
  return <dialog ref={dialog} aria-label="ODS" onCancel={event => { event.preventDefault(); complete() }} className={`ods-opening${leaving ? ' is-leaving' : ''}`}>
    <SplashMetalOrb />
    <div className="ods-opening-content">
    <h1 className="ods-opening-wordmark">ODS</h1>
    <div className="ods-opening-track" aria-hidden="true"><span /></div>
    <p role="status">Opening your workspace</p>
    <button type="button" aria-label="Skip splash screen" onClick={complete}>Continue</button>
    </div>
  </dialog>
}
