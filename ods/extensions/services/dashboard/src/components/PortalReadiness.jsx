import { Link } from 'react-router-dom'
import { pixelReadinessView } from '../lib/pixelReadiness'

export default function PortalReadiness({ readiness }) {
  const view = pixelReadinessView(readiness, true)
  // Unverified release binding remains available in diagnostics; it is not an
  // actionable problem to repeat above every otherwise usable conversation.
  if (!view.attention) return null
  return <p role="alert" aria-label="Runtime readiness"
    className="shrink-0 border-b border-theme-border px-4 py-2 text-xs text-theme-text-secondary sm:px-6">
    <strong>Runtime needs attention. </strong>
    {view.detail}{' '}
    <Link to="/settings?section=access" className="underline">Access settings</Link>
  </p>
}
