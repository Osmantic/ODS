import {CircleAlert} from 'lucide-react'
import PortalStreamingText from './PortalStreamingText'
import './portal-agent-experience.css'

export default function PortalResponseError({content}) {
  return <div className="portal-response-error" role="status"><CircleAlert size={13}/><PortalStreamingText instant>{content==='Request failed'?'The response could not be received. Your conversation is preserved; check the connection before continuing.':content}</PortalStreamingText></div>
}
