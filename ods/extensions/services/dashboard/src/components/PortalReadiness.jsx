import { Link } from 'react-router-dom'
import { pixelReadinessView } from '../lib/pixelReadiness'

export default function PortalReadiness({ readiness }) {
  const view = pixelReadinessView(readiness, true)
  return <p role={view.attention ? 'alert' : 'status'} aria-label="Runtime readiness"
    className="shrink-0 border-b border-theme-border px-4 py-2 text-xs text-amber-300 sm:px-6">
    <strong>{view.attention ? 'Runtime needs attention. ' : 'Runtime readiness unverified. '}</strong>
    {view.detail} Chat availability does not establish permissions or release readiness.{' '}
    <Link to="/settings?section=access" className="underline">Access settings</Link>
  </p>
}
