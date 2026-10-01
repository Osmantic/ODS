// Receipt-matching manifest metadata for navigation/state tests. Artifact byte
// verification and public-origin handshakes have separate transport coverage.
export function previewManifestResponse(preview) {
  const files=Array.from({length:preview.files},(_,index)=>({
    path:index?`assets/file-${index}.txt`:'index.html',
    bytes:index?1:preview.bytes-preview.files+1,
    sha256:index?'c'.repeat(64):preview.entrySha256,
  }))
  const body=JSON.stringify({schemaVersion:1,siteId:preview.siteId,sha256:preview.sha256,bytes:preview.bytes,files})
  return {ok:true,headers:new Map(),arrayBuffer:async()=>new TextEncoder().encode(body).buffer}
}
