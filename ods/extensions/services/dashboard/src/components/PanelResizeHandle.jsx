import { useRef } from 'react'

export default function PanelResizeHandle({ width, onResize, label = 'Resize workspace panel', container = '.portal-workspace', minimum = 320 }) {
  const drag = useRef(null)
  function resize(element, next) {
    const available = element.closest(container)?.clientWidth || 1200
    const maximum = Math.max(minimum, available - 320)
    onResize(Math.max(minimum, Math.min(maximum, next)))
  }
  return <div className="portal-panel-resizer" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={minimum} aria-valuenow={width} tabIndex={0}
    onPointerDown={event => {
      if (event.button !== 0) return
      drag.current = {x:event.clientX, width:event.currentTarget.parentElement.getBoundingClientRect().width}
      event.currentTarget.setPointerCapture(event.pointerId)
      event.preventDefault()
    }}
    onPointerMove={event => { if (drag.current) resize(event.currentTarget, drag.current.width + drag.current.x - event.clientX) }}
    onPointerUp={event => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
    onPointerCancel={() => { drag.current = null }}
    onLostPointerCapture={() => { drag.current = null }}
    onDoubleClick={event => resize(event.currentTarget, 440)}
    onKeyDown={event => {
      if (!['ArrowLeft','ArrowRight','Home'].includes(event.key)) return
      event.preventDefault()
      resize(event.currentTarget, event.key === 'Home' ? 440 : width + (event.key === 'ArrowLeft' ? 32 : -32))
    }} />
}
