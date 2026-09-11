import { useRef } from 'react'

export default function PanelResizeHandle({ width, onResize, label = 'Resize workspace panel', container = '.portal-workspace', minimum = 320 }) {
  const drag = useRef(null)
  function endDrag(event) {
    if (drag.current?.pointerId !== event.pointerId) return
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  function resize(element, next) {
    const available = element.closest(container)?.clientWidth || 1200
    const maximum = Math.max(minimum, available - 320)
    onResize(Math.max(minimum, Math.min(maximum, next)))
  }
  return <div className="portal-panel-resizer" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={minimum} aria-valuenow={width} tabIndex={0}
    onPointerDown={event => {
      if (event.button !== 0 || drag.current) return
      drag.current = {pointerId:event.pointerId, x:event.clientX, width:event.currentTarget.parentElement.getBoundingClientRect().width}
      event.currentTarget.setPointerCapture(event.pointerId)
      event.preventDefault()
    }}
    onPointerMove={event => { if (drag.current?.pointerId === event.pointerId) resize(event.currentTarget, drag.current.width + drag.current.x - event.clientX) }}
    onPointerUp={endDrag}
    onPointerCancel={endDrag}
    onLostPointerCapture={endDrag}
    onDoubleClick={event => resize(event.currentTarget, 440)}
    onKeyDown={event => {
      if (!['ArrowLeft','ArrowRight','Home'].includes(event.key)) return
      event.preventDefault()
      resize(event.currentTarget, event.key === 'Home' ? 440 : width + (event.key === 'ArrowLeft' ? 32 : -32))
    }} />
}
