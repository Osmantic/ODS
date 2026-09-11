import { useId } from 'react'

const palettes = {
  green: ['#a8f7c5', '#29cb77', '#08743d'],
  red: ['#ffb2ad', '#ef5954', '#942822'],
  orange: ['#ffe1a0', '#efa735', '#9b5c13'],
  neutral: ['#c8ccce', '#858b90', '#43484d'],
}

export default function StatusOrb({ tone = 'neutral', size = 10 }) {
  const id = useId().replace(/:/g, '')
  const colors = palettes[tone] || palettes.neutral
  return <svg className="status-orb" width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
    <defs>
      <radialGradient id={`${id}-color`} cx="35%" cy="28%" r="78%"><stop stopColor={colors[0]}/><stop offset=".48" stopColor={colors[1]}/><stop offset="1" stopColor={colors[2]}/></radialGradient>
      <filter id={`${id}-texture`} x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".72" numOctaves="2" seed="9" result="noise"/><feComposite in="noise" in2="SourceGraphic" operator="in" result="grain"/><feBlend in="SourceGraphic" in2="grain" mode="soft-light"/></filter>
    </defs>
    <circle cx="10" cy="10" r="8.5" fill={`url(#${id}-color)`}/>
    <circle cx="10" cy="10" r="8.5" fill={`url(#${id}-color)`} filter={`url(#${id}-texture)`} opacity=".22"/>
    <circle cx="10" cy="10" r="8.1" fill="none" stroke={colors[0]} strokeOpacity=".25" strokeWidth=".6"/>
  </svg>
}
