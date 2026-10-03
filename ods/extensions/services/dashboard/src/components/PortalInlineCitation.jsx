import {useId, useState} from 'react'
import PortalSiteIcon from './PortalSiteIcon'

/** Decorate only links actually present in the answer; icons are not verification. */
export default function PortalInlineCitation({href, children}) {
  const id=useId(), [open,setOpen]=useState(false)
  let url
  try { url=new URL(href); if(!['https:','http:'].includes(url.protocol))return <span>{children}</span> } catch {return <span>{children}</span>}
  const snapshot = href.match(/^http:\/\/(site-[a-f0-9]{24})\.localhost:([1-9][0-9]{0,4})\/\1\/$/)
  const target=snapshot && Number(snapshot[2])<=65535 ? `/pixel-preview/${snapshot[1]}/` : href
  return <span className="portal-citation" onMouseEnter={()=>setOpen(true)} onMouseLeave={()=>setOpen(false)}>
    <a href={target} target="_blank" rel="noopener noreferrer" aria-describedby={open?id:undefined} onFocus={()=>setOpen(true)} onBlur={()=>setOpen(false)} onKeyDown={event=>{if(event.key==='Escape')setOpen(false)}}>
      <PortalSiteIcon href={href}/>{children}<span className="portal-citation-domain" aria-hidden="true">{snapshot ? 'Preview' : url.hostname.replace(/^www\./,'')}</span>
    </a>
    {open && <span id={id} role="tooltip" className="portal-citation-tooltip"><strong>{url.hostname}</strong><span>{target}</span></span>}
  </span>
}
