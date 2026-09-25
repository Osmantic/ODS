import { useEffect, useRef, useState } from 'react'
import {useMascotPreferences} from '../lib/portalMascotPreferences'

// Reuse the original Pixel renderer, including pointer drift and reduced-motion handling.
export default function PixelMascot({ state = 'idle', settled = false, brand = false, interactive = false, name = 'Portal', className = '', preview = false, activityKey = '' }) {
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
  const character = <span ref={element} data-pixel-name={name} data-pixel-brand={brand ? '' : undefined} data-pixel-interactive={interactive ? '' : undefined} className={`pixel-character ${className}`} aria-hidden="true"><svg viewBox="0 0 100 100"><path d="M25 75C14 75 8 68 8 59C8 49 15 42 25 41C26 28 36 20 48 22C58 23 64 30 66 38C77 34 88 41 89 51C97 55 97 67 90 72C86 75 81 75 76 75Z" fill="#dce8f4"/></svg></span>
  return interactive ? <button type="button" className="pixel-pet-button" aria-label={`Play with ${name}`} title={sleeping ? `${name} is resting` : `Play with ${name}`} onClick={() => { setSleeping(false); setPlayCount(value => value + 1); animation.current?.play?.(element.current) }}>{character}</button> : character
}
