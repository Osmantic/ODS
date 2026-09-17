/*!
 * Adapted from UImaxxing Counter Progress Ring, © 2026 Yogi Suria.
 * Free to use, modify and ship in products, including commercial products.
 * Not for republication as a component library or ML training data.
 * https://uimaxx.ing/r/counter-progress-ring.json
 * provenance-mark: uim1-1ea983e5.0d5c62d4
 */
import {useId, useState} from 'react'
import './portal-agent-experience.css'

export default function PortalContextRing({context, capacity, capacityLabel='', pending = false, onRefresh}) {
  const id = useId(), [open, setOpen] = useState(false)
  const valid = value => Number.isSafeInteger(value) && value > 0 && value <= 10_000_000
  const measured = context && Number.isSafeInteger(context.used) && context.used>=0 && context.used<=100_000_000 && valid(context.window)
  const percent = measured ? Math.round(100 * context.used / context.window) : null
  const window = measured ? context.window : valid(capacity) ? capacity : null
  const detail = measured ? `${percent}% full · ${context.used.toLocaleString()} / ${window.toLocaleString()} tokens used`
    : `Token usage unavailable${window ? ` · ${window.toLocaleString()} token capacity` : ''}`
  const compact = value => value >= 1000 ? `${Number((value / 1000).toFixed(1))}k` : String(value)
  const reveal=()=>{setOpen(true);onRefresh?.()}
  return <span className="portal-context" onMouseEnter={reveal} onMouseLeave={()=>setOpen(false)}>
    <button type="button" className="portal-context-trigger" aria-label={detail} aria-describedby={open ? id : undefined}
      onFocus={reveal} onBlur={()=>setOpen(false)} onClick={reveal} onKeyDown={event=>{if(event.key==='Escape')setOpen(false)}}>
      <svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><circle cx="24" cy="24" r="22" strokeWidth="2.8" className="portal-context-track"/>
        {measured && <circle cx="24" cy="24" r="22" pathLength="100" strokeWidth="2.8" className="portal-context-arc" strokeDasharray="100" strokeDashoffset={100-Math.min(percent,100)}/>}
      </svg>
    </button>
    {open && <span role="tooltip" id={id} className="portal-context-tooltip">
      <span className="portal-context-heading">Context window</span>
      {measured ? <><strong>{percent}% used ({Math.max(0,100-percent)}% remaining)</strong><span title={detail}>{compact(context.used)} / {compact(window)} tokens used</span>{pending && <small>Last measured call · updating</small>}</>
        : <><strong>Token usage unavailable</strong><span>{window ? `${compact(window)} token capacity` : capacityLabel || 'Waiting for model data'}</span></>}
    </span>}
  </span>
}
