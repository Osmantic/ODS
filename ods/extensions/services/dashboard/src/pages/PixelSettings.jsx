import { Navigate, useSearchParams } from 'react-router-dom'

const sections = new Set(['connections', 'access', 'sharing', 'pixel-diagnostics'])

export default function PixelSettings() {
  const [params] = useSearchParams()
  const section = sections.has(params.get('section')) ? params.get('section') : 'connections'
  return <Navigate to={`/settings?section=${section}`} replace />
}
