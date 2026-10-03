import {useState} from 'react'
import {Globe2} from 'lucide-react'
import './portal-site-icon.css'

// Request only a public hostname, never the source's path, query or credentials.
// The fixed favicon cache also avoids fetching arbitrary sites from chat Markdown.
export function siteIconUrl(href) {
  try {
    const url=new URL(href),host=url.hostname.toLowerCase()
    if(!['https:','http:'].includes(url.protocol) || url.username || url.password) return null
    if(host.length>253 || !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/.test(host)) return null
    if(/\.(?:localhost|local|internal|intranet|lan|home|corp|test|invalid|example|onion)$/.test(host)) return null
    return `https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=${encodeURIComponent(`https://${host}`)}&size=32`
  } catch {return null}
}

function SiteIcon({src}) {
  const [loaded,setLoaded]=useState(false),[failed,setFailed]=useState(false)
  return <span className="portal-site-icon" aria-hidden="true">
    {!loaded && <Globe2 size={14}/>}
    {src && !failed && <img src={src} alt="" width="16" height="16" loading="lazy" decoding="async" referrerPolicy="no-referrer" data-loaded={loaded} onLoad={()=>setLoaded(true)} onError={()=>{setLoaded(false);setFailed(true)}}/>}
  </span>
}

export default function PortalSiteIcon({href}) {
  const src=siteIconUrl(href)
  return <SiteIcon key={src || 'local'} src={src}/>
}
