import { useEffect, useRef } from 'react'

// Reuse the original Pixel renderer, including gaze and reduced-motion handling.
export default function PixelMascot({ state = 'idle', settled = false, brand = false, className = '' }) {
  const element = useRef(null)
  const animation = useRef(null)
  useEffect(() => {
    const node = element.current
    const renderer = globalThis.PixelMascot
    animation.current = renderer
    renderer?.mount(node, { state, settled })
    return () => {renderer?.destroy(node); animation.current = null}
    // Mount once: state changes must retain the renderer's spring/pose.
  }, [])
  useEffect(() => {animation.current?.setState(element.current, state, { settled })}, [state, settled])
  return <span ref={element} data-pixel-brand={brand ? '' : undefined} className={`pixel-character ${className}`} aria-hidden="true"><svg viewBox="0 0 64 64"><rect x="8" y="9" width="48" height="46" rx="15" fill="#e5e5e4"/><path d="M24 26v9m16-9v9" stroke="#18191b" strokeWidth="5" strokeLinecap="round"/></svg></span>
}
