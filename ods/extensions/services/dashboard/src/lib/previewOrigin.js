import {loadSnapshotFiles, loadArtifactBytes, readBoundedBytes, sha256} from './pixelArtifacts'

export function canonicalPreviewOrigin(preview) {
  if (!/^site-[a-f0-9]{24}$/.test(preview?.siteId) || !/^[a-f0-9]{64}$/.test(preview?.sha256)
    || preview.siteId !== `site-${preview.sha256.slice(0,24)}`
    || !Number.isInteger(preview.port) || preview.port < 1 || preview.port > 65535) return null
  const url = `http://${preview.siteId}.localhost:${preview.port}/${preview.siteId}/`
  return preview.url === url ? new URL(url) : null
}

export async function resolveVerifiedPreview(preview, fallback, signal) {
  const target=canonicalPreviewOrigin(preview)
  if (!target) throw new Error('Invalid preview origin')
  const files=await loadSnapshotFiles(preview,signal)
  const entry=files.find(file=>file.path==='index.html')
  const trustedEntry=await loadArtifactBytes(preview,entry,signal)
  const html=new TextDecoder().decode(trustedEntry)
  const rootRelative=/(?:src|href|action|poster)\s*=\s*["']\/(?!\/)/i.test(html)
    || files.some(file=>file.path.startsWith('_next/'))
  const options={signal,cache:'no-store',credentials:'omit',redirect:'error',referrerPolicy:'no-referrer'}
  try {
    const response=await fetch(new URL('__ods_manifest__.json',target).href,options)
    const bytes=await readBoundedBytes(response,256*1024)
    const manifest=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))
    if (manifest.schemaVersion!==1 || manifest.siteId!==preview.siteId || manifest.sha256!==preview.sha256
      || manifest.bytes!==preview.bytes || !Array.isArray(manifest.files) || manifest.files.length!==files.length
      || manifest.files.some((file,index)=>file.path!==files[index].path || file.bytes!==files[index].bytes || file.sha256!==files[index].sha256)) {
      throw new Error('Preview origin mismatch')
    }
    if(signal.aborted)throw new DOMException('Aborted','AbortError')
    // Test root routing too: framework chunks and nested routes must share this
    // isolated origin, not the Dashboard's unrelated root namespace.
    const entryResponse=await fetch(new URL('/',target).href,options)
    const entryBytes=await readBoundedBytes(entryResponse,4*1024*1024)
    if(entryBytes.byteLength!==entry.bytes || await sha256(entryBytes)!==entry.sha256)throw new Error('Preview entry mismatch')
    if(signal.aborted)throw new DOMException('Aborted','AbortError')
    return {...fallback,url:target.href,frameUrl:new URL('__ods_view__.html',target).href,route:'verified-site-origin'}
  } catch(error) {
    // A public-origin timeout may use the already verified relative-site relay.
    // Cancellation still rejects so obsolete probes cannot become results.
    if(signal.aborted && signal.reason?.name!=='TimeoutError')throw error
    if(rootRelative)return {...fallback,frameUrl:null,unavailable:true}
    return fallback
  }
}
