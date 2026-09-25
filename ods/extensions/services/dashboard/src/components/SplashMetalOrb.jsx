import {useEffect, useId, useRef} from 'react'
import {gsap} from 'gsap'
import {CustomEase} from 'gsap/CustomEase'

gsap.registerPlugin(CustomEase)

// Restore the original ODS opening's orbit choreography (4b3d668c),
// with a chrome reflection ramp instead of the former neon palette.
export default function SplashMetalOrb() {
  const svgRef = useRef(null)
  const gradient = useId().replace(/:/g, '')
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (media?.matches) return
    const svg = svgRef.current
    let cleanup
    const ctx = gsap.context(() => {
      const ease = CustomEase.create('odsMetalOrb', 'M0,0 C0.2,0 0.432,0.147 0.507,0.374 0.59,0.629 0.822,1 1,1')
      const orbit = gsap.timeline({repeat: -1})
      svg.querySelectorAll('.ell').forEach((el, index) => {
        const offset = index + 1
        gsap.set(el, {opacity: 1 - offset / 34})
        const loop = gsap.timeline({repeat: -1, defaults: {duration: 1, ease}})
          .to(el, {attr: {rx: 180 + offset * 3.8, ry: 180 - offset * 2.3}, strokeWidth: 5})
          .to(el, {attr: {rx: 180, ry: 180}, strokeWidth: 36})
          .to(el, {duration: 2, rotation: -360, transformOrigin: '50% 50%'}, 0)
          .timeScale(0.5)
        orbit.add(loop, offset / 31)
      })
      // Begin with a formed object, avoiding an empty first frame.
      orbit.totalTime(1.8)
      const sync = () => orbit.paused(document.hidden || Boolean(media?.matches))
      sync()
      document.addEventListener('visibilitychange', sync)
      media?.addEventListener?.('change', sync)
      cleanup = () => {
        document.removeEventListener('visibilitychange', sync)
        media?.removeEventListener?.('change', sync)
      }
    }, svg)
    return () => {cleanup?.(); ctx.revert()}
  }, [])
  return <svg ref={svgRef} className="ods-opening-orb" viewBox="0 0 800 600" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={gradient} x1="8%" y1="0%" x2="90%" y2="100%">
        <stop offset="0" stopColor="#202328"/>
        <stop offset=".19" stopColor="#8d969e"/>
        <stop offset=".29" stopColor="#f4f6f8"/>
        <stop offset=".34" stopColor="#555c64"/>
        <stop offset=".48" stopColor="#121518"/>
        <stop offset=".57" stopColor="#656e78"/>
        <stop offset=".65" stopColor="#e9edf1"/>
        <stop offset=".69" stopColor="#fff"/>
        <stop offset=".77" stopColor="#59616a"/>
        <stop offset="1" stopColor="#171a1d"/>
      </linearGradient>
    </defs>
    {Array.from({length: 31}, (_, index) => <ellipse key={index} className="ell" cx="400" cy="300" rx="180" ry="180" fill="none" stroke={`url(#${gradient})`} strokeWidth="2"/>)}
  </svg>
}
