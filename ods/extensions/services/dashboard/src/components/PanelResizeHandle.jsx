import { useCallback, useEffect, useRef, useState } from 'react'

export default function PanelResizeHandle({ width, onResize, label = 'Resize workspace panel', container = '.portal-workspace', minimum = 320 }) {
  const drag = useRef(null)
  const handle = useRef(null)
  const [range, setRange] = useState({current: width, maximum: minimum})
  const measure = useCallback(element => {
    const available = element.closest(container)?.clientWidth || 1200
    const panel = element.parentElement
    const cssLimit = window.getComputedStyle(panel).maxWidth
    const limit = cssLimit.endsWith('%')
      ? parseFloat(cssLimit) / 100 * (panel.parentElement?.clientWidth || available)
      : cssLimit.endsWith('px') ? parseFloat(cssLimit) : Infinity
    const maximum = Math.max(minimum, Math.min(available - 320, limit))
    const current = panel.getBoundingClientRect().width || width
    return {current, maximum}
  }, [width, container, minimum])
  useEffect(() => {
    const element = handle.current
    const update = () => setRange(measure(element))
    update()
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    observer?.observe(element.parentElement)
    const workspace = element.closest(container)
    if (workspace) observer?.observe(workspace)
    window.addEventListener('resize', update)
    return () => { observer?.disconnect(); window.removeEventListener('resize', update) }
  }, [measure, container])
  function endDrag(event) {
    if (drag.current?.pointerId !== event.pointerId) return
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  function resize(element, next) {
    const {maximum} = measure(element)
    onResize(Math.max(minimum, Math.min(maximum, next)))
  }
  return <div ref={handle} className="portal-panel-resizer" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={minimum} aria-valuenow={range.current} aria-valuemax={Math.max(range.current, range.maximum)} aria-valuetext={`${Math.round(range.current)} pixels`} tabIndex={0}
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
      resize(event.currentTarget, event.key === 'Home' ? 440 : measure(event.currentTarget).current + (event.key === 'ArrowLeft' ? 32 : -32))
    }} />
}
