import { useEffect, useRef, useState } from 'react'
import {useMascotPreferences} from '../lib/portalMascotPreferences'
import {DEFAULT_ASSISTANT_NAME} from '../lib/portalIdentity'

// Reuse the original Pixel renderer, including gaze and reduced-motion handling.
export default function PixelMascot({ state = 'idle', settled = false, brand = false, interactive = false, name = DEFAULT_ASSISTANT_NAME, className = '', preview = false, activityKey = '' }) {
  const preferences = useMascotPreferences()
  const visible = preview || preferences.enabled
  const element = useRef(null)
  const animation = useRef(null)
  const [sleeping, setSleeping] = useState(false)
  const [playCount, setPlayCount] = useState(0)
  useEffect(() => {
    if (!visible || preview || !interactive || state !== 'idle' || !preferences.sleepAfterSeconds) { setSleeping(false); return }
    setSleeping(false)
    // Conversation activity, not activity elsewhere in the app, starts a new minute.
    const deadline = Date.now() + preferences.sleepAfterSeconds * 1000
    const timer = setTimeout(() => setSleeping(true), preferences.sleepAfterSeconds * 1000)
    const checkDeadline = () => { if (!document.hidden && Date.now() >= deadline) setSleeping(true) }
    document.addEventListener('visibilitychange', checkDeadline)
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', checkDeadline) }
  }, [interactive, state, visible, preview, preferences.sleepAfterSeconds, activityKey, playCount])
  useEffect(() => {
    const node = element.current
    if (!node || !visible) return
    const renderer = globalThis.PixelMascot
    animation.current = renderer
    renderer?.mount(node, { state, settled, static: !preferences.animated })
    return () => {renderer?.destroy(node); animation.current = null}
    // Mount once: state changes must retain the renderer's spring/pose.
  }, [visible, preferences.animated])
  useEffect(() => {animation.current?.setState(element.current, sleeping && state === 'idle' ? 'sleeping' : state, { settled }); if (element.current) element.current.title = `${name} · ${sleeping && state === 'idle' ? 'sleeping' : state}`}, [state, settled, sleeping, name, visible, preferences.animated])
  if (!visible) return null
  const character = <span ref={element} data-pixel-name={name} data-pixel-brand={brand ? '' : undefined} data-pixel-interactive={interactive ? '' : undefined} className={`pixel-character ${className}`} aria-hidden="true"><svg viewBox="0 0 100 100"><rect x="19" y="20" width="62" height="60" rx="19" fill="#e5e5e4"/><path d="M40 41.75v14.5m20-14.5v14.5" stroke="#18191b" strokeWidth="9" strokeLinecap="round"/></svg></span>
  return interactive ? <button type="button" className="pixel-pet-button" aria-label={`Play with ${name}`} title={sleeping ? `${name} is resting` : `Play with ${name}`} onClick={() => { setSleeping(false); setPlayCount(value => value + 1); animation.current?.play?.(element.current) }}>{character}</button> : character
}
