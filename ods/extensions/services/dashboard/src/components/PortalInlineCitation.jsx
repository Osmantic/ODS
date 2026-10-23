import {useId, useState} from 'react'
import PortalSiteIcon from './PortalSiteIcon'
import PixelArtifactDownload from './PixelArtifactDownload'
import {deliveredArtifactForDownloadHref,deliveredArtifactForDownloadName,deliveredArtifactForHref} from '../lib/pixelDeliveredArtifacts'

/** Decorate only links actually present in the answer; icons are not verification. */
export default function PortalInlineCitation({href, children, artifacts, node}) {
  const id=useId(), [open,setOpen]=useState(false)
  const filename=node?.properties?.['data-ods-download-filename']
  const download=node?.properties?.['data-ods-download-intent']===true
    ? (filename ? deliveredArtifactForDownloadName(filename,artifacts) : deliveredArtifactForDownloadHref(href,artifacts)) : null
  if(download)return <PixelArtifactDownload key={`${download.siteId}/${download.file.path}/${download.file.sha256}/${download.file.bytes}`} preview={download} file={download.file} inline>Download {download.file.path}</PixelArtifactDownload>
  const artifact=filename ? null : deliveredArtifactForHref(href,artifacts)
  if(artifact)return <PixelArtifactDownload key={`${artifact.siteId}/${artifact.file.path}/${artifact.file.sha256}/${artifact.file.bytes}`} preview={artifact} file={artifact.file} inline>{children}</PixelArtifactDownload>
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
