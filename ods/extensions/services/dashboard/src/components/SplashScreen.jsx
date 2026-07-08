import { useCallback, useEffect, useRef, useState } from 'react'
import { gsap } from 'gsap'

const SPLASH_DURATION_MS = 2800
const EXIT_PAUSE_MS = 300
const FADE_DURATION_MS = 600
const LOW_END_ELLIPSE_COUNT = 14
const STANDARD_ELLIPSE_COUNT = 22
const visuallyHiddenStyle = {
  position: 'absolute',
  width: '1px',
  height: '1px',
  padding: 0,
  margin: '-1px',
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
}

function prefersReducedMotion() {
  try {
    return typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function isLowPerformanceDevice() {
  if (typeof navigator === 'undefined') return false

  const memory = typeof navigator.deviceMemory === 'number' ? navigator.deviceMemory : Infinity
  const cores = typeof navigator.hardwareConcurrency === 'number' ? navigator.hardwareConcurrency : Infinity
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection

  return Boolean(connection?.saveData) || memory <= 4 || cores <= 4
}

function startAnimationFrame(callback) {
  if (typeof requestAnimationFrame === 'function') {
    return requestAnimationFrame(callback)
  }

  return setTimeout(() => callback(performance.now()), 16)
}

function stopAnimationFrame(frameId) {
  if (typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(frameId)
    return
  }

  clearTimeout(frameId)
}

// ─── Orb SVG animation — exact port of codepen.io/chrisgannon/pen/ZYQjZBr ───
function OrbBackground({ reduced, lowPerformance }) {
  const svgRef = useRef(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg || reduced) return

    const allEll = Array.from(svg.querySelectorAll('.ell'))
    const _ca = ['#f72585', '#7209b7', '#3a0ca3', '#4361ee', '#4cc9f0', '#D9F4FC']
    const rxFactor = lowPerformance ? 2.4 : 3.2
    const ryFactor = lowPerformance ? 1.5 : 2
    const strokeStart = lowPerformance ? 8 : 10
    const strokeEnd = lowPerformance ? 56 : 84
    const colorInterp = gsap.utils.interpolate(_ca)

    const ctx = gsap.context(() => {
      gsap.set(svg, { visibility: 'visible' })

      function animateEllipse(el, index) {
        const offset = index + 1
        const timeline = gsap.timeline({
          defaults: { duration: lowPerformance ? 1.25 : 1, ease: 'sine.inOut' },
          repeat: -1,
        })

        gsap.set(el, {
          opacity: 1 - offset / allEll.length,
          stroke: colorInterp(offset / allEll.length),
        })

        timeline
          .to(el, {
            attr: { rx: `+=${offset * rxFactor}`, ry: `-=${offset * ryFactor}` },
            strokeWidth: strokeStart,
            ease: 'power2.in',
          })
          .to(el, {
            strokeWidth: strokeEnd,
            attr: { rx: `-=${offset * rxFactor}`, ry: `+=${offset * ryFactor}` },
            ease: 'power2.out',
          })
          .to(el, {
            duration: lowPerformance ? 2.5 : 2,
            rotation: -360,
            transformOrigin: '50% 50%',
            ease: 'none',
          }, 0)
          .from(el, {
            duration: lowPerformance ? 1.1 : 0.9,
            scale: 0,
            transformOrigin: '50% 50%',
            ease: 'power2.out',
          }, 0)
          .timeScale(lowPerformance ? 0.42 : 0.54)

        timeline.progress((offset / allEll.length) * 0.35)
      }

      allEll.forEach(animateEllipse)
    }, svg)

    return () => ctx.revert()
  }, [lowPerformance, reduced])

  if (reduced) return null

  return (
    <svg
      ref={svgRef}
      id="splashSVG"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 800 600"
      aria-hidden="true"
      focusable="false"
      style={{
        width: '100%',
        height: '100%',
        visibility: 'hidden',
        position: 'absolute',
        inset: 0,
      }}
    >
      {Array.from({ length: lowPerformance ? LOW_END_ELLIPSE_COUNT : STANDARD_ELLIPSE_COUNT }, (_, i) => (
        <ellipse
          key={i}
          className="ell"
          cx="400"
          cy="300"
          rx="180"
          ry="180"
          fill="none"
          style={{ strokeWidth: 0, strokeLinecap: 'round', strokeLinejoin: 'round' }}
        />
      ))}
    </svg>
  )
}

// ─── Splash Screen ────────────────────────────────────────────────────────────
export default function SplashScreen({ onComplete }) {
  const [reduced] = useState(prefersReducedMotion)
  const [lowPerformance] = useState(isLowPerformanceDevice)

  const [progress, setProgress] = useState(0)
  const [glitching, setGlitching] = useState(false)
  const [done, setDone] = useState(false)
  const rafRef = useRef(null)
  const startRef = useRef(null)
  const completionRef = useRef(false)
  const timeoutsRef = useRef([])

  const clearScheduledWork = useCallback(() => {
    if (rafRef.current !== null) {
      stopAnimationFrame(rafRef.current)
      rafRef.current = null
    }

    timeoutsRef.current.forEach(clearTimeout)
    timeoutsRef.current = []
  }, [])

  const finishSplash = useCallback((delay = FADE_DURATION_MS) => {
    if (completionRef.current) return

    completionRef.current = true
    clearScheduledWork()
    setGlitching(false)
    setProgress(100)
    setDone(true)

    if (delay <= 0) {
      onComplete?.()
      return
    }

    const timeoutId = setTimeout(() => onComplete?.(), delay)
    timeoutsRef.current.push(timeoutId)
  }, [clearScheduledWork, onComplete])

  useEffect(() => {
    return () => clearScheduledWork()
  }, [clearScheduledWork])

  // If reduced motion, complete immediately without any animation
  useEffect(() => {
    if (reduced) {
      finishSplash(0)
    }
  }, [finishSplash, reduced])

  // Progress bar
  useEffect(() => {
    if (reduced) return
    startRef.current = performance.now()
    const duration = SPLASH_DURATION_MS
    function tick(now) {
      const elapsed = now - startRef.current
      const p = Math.min(elapsed / duration, 1)
      const eased = 1 - Math.pow(1 - p, 3)
      setProgress(Math.floor(eased * 100))
      if (p < 1) {
        rafRef.current = startAnimationFrame(tick)
      } else {
        setProgress(100)
        const timeoutId = setTimeout(() => finishSplash(), EXIT_PAUSE_MS)
        timeoutsRef.current.push(timeoutId)
      }
    }
    rafRef.current = startAnimationFrame(tick)
    return () => clearScheduledWork()
  }, [clearScheduledWork, finishSplash, reduced])

  // Glitch
  useEffect(() => {
    if (reduced) return
    let timeoutId
    function schedule() {
      timeoutId = setTimeout(() => {
        setGlitching(true)
        const toggleId = setTimeout(() => {
          setGlitching(false)
          schedule()
        }, 80 + Math.random() * 120)
        timeoutsRef.current.push(toggleId)
      }, Math.random() * 900 + 200)
      timeoutsRef.current.push(timeoutId)
    }
    schedule()
    return () => clearTimeout(timeoutId)
  }, [reduced])

  const skip = useCallback(() => finishSplash(300), [finishSplash])

  useEffect(() => {
    if (reduced) return
    const onKey = (e) => { if (e.key === 'Escape') skip() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [reduced, skip])

  if (reduced) return null

  const glitchChars = '!@#$%^&*░▒▓█▄▀■□▪'
  const title = 'ODS'
  // Glitch chars are decorative — aria-label on the parent exposes the real name
  const displayTitle = glitching
    ? title.split('').map(ch =>
        Math.random() < 0.18 ? glitchChars[Math.floor(Math.random() * glitchChars.length)] : ch
      ).join('')
    : title

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="ods-splash-title"
      aria-describedby="ods-splash-status ods-splash-hint"
      onClick={skip}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: '#000',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden',
        opacity: done ? 0 : 1,
        transition: done ? 'opacity 0.6s ease' : 'none',
        pointerEvents: done ? 'none' : 'all',
        cursor: 'pointer',
      }}
    >
      {/* Decorative orb — hidden from assistive tech */}
      <div style={{ position: 'absolute', inset: 0, opacity: 0.75 }} aria-hidden="true">
        <OrbBackground reduced={reduced} lowPerformance={lowPerformance} />
      </div>

      {/* Content */}
      <div style={{
        position: 'relative', zIndex: 2,
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        gap: '2rem', width: '100%', maxWidth: '520px', padding: '0 2rem',
      }}>
        <p
          id="ods-splash-status"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          aria-busy={!done}
          style={visuallyHiddenStyle}
        >
          {done ? 'ODS is ready.' : `Loading ODS. ${progress}% complete.`}
        </p>

        <h1 id="ods-splash-title" style={{ position: 'relative', userSelect: 'none', margin: 0 }}>
          <span style={visuallyHiddenStyle}>ODS</span>
          {glitching && (
            <span style={{
              position: 'absolute', top: 0, left: '2px', color: '#f72585',
              fontFamily: "'JetBrains Mono','Courier New',monospace",
              fontSize: 'clamp(2rem,6vw,3.5rem)', fontWeight: 900, letterSpacing: '0.05em',
              clipPath: 'polygon(0 20%,100% 20%,100% 45%,0 45%)',
              opacity: 0.9, pointerEvents: 'none',
            }} aria-hidden="true">{displayTitle}</span>
          )}
          {glitching && (
            <span style={{
              position: 'absolute', top: 0, left: '-3px', color: '#4cc9f0',
              fontFamily: "'JetBrains Mono','Courier New',monospace",
              fontSize: 'clamp(2rem,6vw,3.5rem)', fontWeight: 900, letterSpacing: '0.05em',
              clipPath: 'polygon(0 60%,100% 60%,100% 80%,0 80%)',
              opacity: 0.85, pointerEvents: 'none',
            }} aria-hidden="true">{displayTitle}</span>
          )}
          <span style={{
            fontFamily: "'JetBrains Mono','Courier New',monospace",
            fontSize: 'clamp(2rem,6vw,3.5rem)', fontWeight: 900, letterSpacing: '0.05em',
            background: 'linear-gradient(135deg,#e4e4e7 0%,#a78bfa 50%,#4cc9f0 100%)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
            backgroundClip: 'text', display: 'inline-block',
            filter: glitching ? 'blur(0.5px)' : 'none', transition: 'filter 0.05s',
          }} aria-hidden="true">{displayTitle}</span>
        </h1>

        <p style={{
          color: '#71717a', fontSize: '0.85rem', letterSpacing: '0.2em',
          textTransform: 'uppercase',
          fontFamily: "'JetBrains Mono','Courier New',monospace",
          margin: '-1.2rem 0 0',
        }}>Local AI Platform</p>

        {/* Progress — accessible via parent role="status" + aria-label */}
        <div style={{ width: '100%' }} aria-hidden="true">
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <span style={{ color: '#52525b', fontSize: '0.7rem', letterSpacing: '0.15em', textTransform: 'uppercase', fontFamily: 'monospace' }}>
              Initializing
            </span>
            <span style={{
              fontFamily: "'JetBrains Mono',monospace", fontSize: '0.8rem', fontWeight: 700,
              color: progress === 100 ? '#4cc9f0' : '#a78bfa', transition: 'color 0.3s',
            }}>{progress}%</span>
          </div>
          <div style={{ width: '100%', height: '3px', background: '#27272a', borderRadius: '999px', overflow: 'hidden', position: 'relative' }}>
            <div style={{
              position: 'absolute', left: 0, top: 0, height: '100%', width: `${progress}%`,
              background: 'linear-gradient(90deg,#7209b7,#4361ee,#4cc9f0)',
              borderRadius: '999px', transition: 'width 0.1s linear',
              boxShadow: '0 0 12px #4cc9f090',
            }} />
          </div>
        </div>

        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            skip()
          }}
          aria-label="Skip splash screen"
          style={{
            border: '1px solid #27272a',
            borderRadius: '999px',
            padding: '0.7rem 1rem',
            background: 'rgba(24,24,27,0.92)',
            color: '#e4e4e7',
            fontFamily: "'JetBrains Mono','Courier New',monospace",
            fontSize: '0.75rem',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
          }}
        >
          Skip intro
        </button>

        <p
          id="ods-splash-hint"
          style={{
          color: '#3f3f46', fontSize: '0.65rem', letterSpacing: '0.15em',
          textTransform: 'uppercase', fontFamily: 'monospace', margin: '-0.5rem 0 0',
        }}
        >
          Click or press Esc to skip
        </p>
      </div>
      {/* Google Fonts CDN removed — violates CSP font-src policy and leaks IPs.
          Font stack falls back to JetBrains Mono / Courier New (system). */}
    </div>
  )
}
