import { useEffect, useId, useState } from 'react'

// Native SVG keeps the original icon paths and avoids a WebGL canvas per metric.
export default function MetalMetricIcon({ icon: Icon, size = 17, className = '' }) {
  const id = `metric-metal-${useId().replace(/:/g,'')}`
  const [still, setStill] = useState(true)
  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!preference) return
    const update = () => setStill(preference.matches)
    update()
    preference.addEventListener?.('change',update)
    return () => preference.removeEventListener?.('change',update)
  },[])
  return <Icon size={size} strokeWidth={2} stroke={`url(#${id})`} aria-hidden="true" className={`dashboard-metal-icon ${className}`}>
    <defs><linearGradient id={id} gradientUnits="userSpaceOnUse" x1="-24" y1="0" x2="24" y2="16" spreadMethod="reflect">
      <stop offset="0" stopColor="#7e878e"/>
      <stop offset=".2" stopColor="#d8dfe4"/>
      <stop offset=".32" stopColor="#ffffff"/>
      <stop offset=".44" stopColor="#7f8993"/>
      <stop offset=".59" stopColor="#e8edf0"/>
      <stop offset=".76" stopColor="#929da5"/>
      <stop offset="1" stopColor="#c4cdd4"/>
      {!still && <animateTransform attributeName="gradientTransform" type="translate" values="-12 0;12 0;-12 0" dur="6s" repeatCount="indefinite" calcMode="spline" keyTimes="0;.5;1" keySplines=".42 0 .58 1;.42 0 .58 1"/>}
    </linearGradient></defs>
  </Icon>
}
