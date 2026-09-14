import { useCallback, useEffect, useRef, useState } from 'react'
import SplashMetalOrb from './SplashMetalOrb'

export default function SplashScreen({ onComplete, preview = false }) {
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
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      complete()
      return
    }
    const timer = preview ? null : window.setTimeout(complete, 3200)
    const onKey = event => { if (event.key === 'Escape') complete() }
    window.addEventListener('keydown', onKey)
    return () => { window.clearTimeout(timer); window.clearTimeout(exitTimer.current); window.removeEventListener('keydown', onKey) }
  }, [complete, preview])
  return <div role="dialog" aria-modal="true" aria-label="ODS" className={`ods-opening${leaving ? ' is-leaving' : ''}`}>
    <SplashMetalOrb />
    <div className="ods-opening-content">
    <h1 className="ods-opening-wordmark">ODS</h1>
    <div className="ods-opening-track" aria-hidden="true"><span /></div>
    <p role="status">Opening your workspace</p>
    <button type="button" aria-label="Skip splash screen" onClick={complete}>Continue</button>
    </div>
  </div>
}
