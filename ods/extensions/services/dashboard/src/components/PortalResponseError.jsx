import {CircleAlert} from 'lucide-react'
import PortalStreamingText from './PortalStreamingText'
import './portal-agent-experience.css'

export default function PortalResponseError({content}) {
  const message = content === 'Request failed'
    ? 'The response could not be received. Your conversation is preserved; check the connection before continuing.'
    : (() => {
      const value = String(content || 'The response could not be received.')
      return value.length > 500 ? `${value.slice(0, 499)}…` : value
    })()
  return <div className="portal-response-error" role="status"><CircleAlert size={13}/><PortalStreamingText instant>{message}</PortalStreamingText></div>
}
