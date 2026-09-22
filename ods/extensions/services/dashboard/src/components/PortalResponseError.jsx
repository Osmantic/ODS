import {CircleAlert} from 'lucide-react'
import './portal-agent-experience.css'

const GENERIC_ERROR = 'The response could not be received. Your conversation is preserved; check the connection before continuing.'
const MAX_ERROR_LENGTH = 500

function errorMessage(content) {
  const normalized = Array.from(String(content ?? ''), character => {
    const code = character.charCodeAt(0)
    return code < 32 || (code >= 127 && code <= 159) ? ' ' : character
  }).join('').replace(/\s+/g, ' ').trim()
  if (!normalized || normalized === 'Request failed') return GENERIC_ERROR
  return normalized.length > MAX_ERROR_LENGTH
    ? `${normalized.slice(0, MAX_ERROR_LENGTH).trimEnd()}…`
    : normalized
}

export default function PortalResponseError({content}) {
  return <div className="portal-response-error" role="status"><CircleAlert size={13}/><span className="portal-streaming-text">{errorMessage(content)}</span></div>
}
