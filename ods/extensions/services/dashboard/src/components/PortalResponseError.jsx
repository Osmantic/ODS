import {useEffect,useState} from 'react'
import {CircleAlert} from 'lucide-react'
import HelpLink from './HelpLink'
import PortalStreamingText from './PortalStreamingText'
import './portal-agent-experience.css'

const GENERIC_FAILURE='Portal could not complete the response.'

// A reply that failed without a known cause asks why the latest model call
// failed. The relay reports only its HTTP status, and the dashboard turns that
// into a cause and a next step (fleet drills: a down API, a refused key and a
// model the key may not use all read the same).
function useModelFailureCause(content) {
  const [cause,setCause]=useState('')
  useEffect(()=>{
    if(typeof content!=='string' || !content.startsWith(GENERIC_FAILURE))return undefined
    const controller=new AbortController()
    Promise.resolve().then(()=>fetch('/api/pixel/model-failure',{signal:controller.signal}))
      .then(response=>response?.ok ? response.json() : null)
      .then(value=>{if(!controller.signal.aborted && typeof value?.message==='string')setCause(value.message)})
      .catch(()=>{})
    return ()=>controller.abort()
  },[content])
  return cause
}

export default function PortalResponseError({content}) {
  const cause=useModelFailureCause(content)
  return <div className="portal-response-error" role="status"><CircleAlert size={13}/><PortalStreamingText instant>{content==='Request failed'?'The response could not be received. Your conversation is preserved; check the connection before continuing.':content}</PortalStreamingText>{cause && <p className="portal-response-cause">{cause}</p>} <HelpLink label="Still stuck? Get help on Discord"/></div>
}
