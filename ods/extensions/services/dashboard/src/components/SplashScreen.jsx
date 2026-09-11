import { useCallback, useEffect, useRef } from 'react'
import ODSLogo from './ODSLogo'

export default function SplashScreen({ onComplete }) {
  const completed = useRef(false)
  const complete = useCallback(() => {
    if (completed.current) return
    completed.current = true
    onComplete?.()
  }, [onComplete])
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      complete()
      return
    }
    const timer = window.setTimeout(complete, 1400)
    const onKey = event => { if (event.key === 'Escape') complete() }
    window.addEventListener('keydown', onKey)
    return () => { window.clearTimeout(timer); window.removeEventListener('keydown', onKey) }
  }, [complete])
  return <div role="dialog" aria-modal="true" aria-label="ODS" className="ods-opening">
    <div className="ods-opening-emblem"><ODSLogo /></div>
    <h1 className="ods-opening-wordmark">ODS</h1>
    <div className="ods-opening-track" aria-hidden="true"><span /></div>
    <p>Opening your workspace</p>
    <button type="button" aria-label="Skip splash screen" onClick={complete}>Continue</button>
  </div>
}
