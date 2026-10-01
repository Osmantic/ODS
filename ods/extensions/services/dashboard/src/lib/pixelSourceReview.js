import {isArtifactPath, readBoundedBytes, sha256} from './pixelArtifacts'

export function validSourceReview(value, directory) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join(',') === 'bytes,files,omitted,relativeDirectory,schemaVersion,sha256,sourceId'
    && value.schemaVersion === 1 && isArtifactPath(value.relativeDirectory)
    && value.relativeDirectory.length <= 512 && value.relativeDirectory.split('/').length <= 12
    && (directory === value.relativeDirectory || directory?.startsWith(value.relativeDirectory + '/'))
    && /^[a-f0-9]{64}$/.test(value.sha256) && value.sourceId === 'source-' + value.sha256.slice(0,24)
    && Number.isInteger(value.files) && value.files >= 1 && value.files <= 128
    && Number.isInteger(value.bytes) && value.bytes >= 0 && value.bytes <= 1024*1024
    && value.omitted && typeof value.omitted === 'object' && !Array.isArray(value.omitted)
    && Object.keys(value.omitted).sort().join(',') === 'directories,files,sensitiveFiles'
    && Object.values(value.omitted).every(n => Number.isInteger(n) && n >= 0 && n <= 16384)
}

export async function loadSourceReview(preview, signal) {
  const receipt=preview?.source
  if (!/^site-[a-f0-9]{24}$/.test(preview?.siteId) || !validSourceReview(receipt,preview.relativeDirectory)) throw new Error('Invalid source review')
  const response=await fetch(`/pixel-preview/${preview.siteId}/__ods_source__/${receipt.sourceId}.json`,{signal,cache:'no-store'})
  const bytes=await readBoundedBytes(response,4*1024*1024)
  if (await sha256(bytes)!==receipt.sha256) throw new Error('Unverified source snapshot')
  const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))
  if (!value || Object.keys(value).sort().join(',')!=='bytes,files,omitted,relativeDirectory,schemaVersion,scope,siteId'
    || value.schemaVersion!==1 || value.scope!=='captured-project-source' || value.siteId!==preview.siteId
    || value.relativeDirectory!==receipt.relativeDirectory || value.bytes!==receipt.bytes
    || !value.omitted || Object.keys(value.omitted).sort().join(',')!=='directories,files,sensitiveFiles'
    || Object.keys(receipt.omitted).some(key=>value.omitted[key]!==receipt.omitted[key])
    || !Array.isArray(value.files) || value.files.length!==receipt.files) throw new Error('Source identity mismatch')
  const seen=new Set();let total=0
  for(const file of value.files) {
    if(!file || Object.keys(file).sort().join(',')!=='bytes,path,sha256,text'
      || !isArtifactPath(file.path) || seen.has(file.path) || typeof file.text!=='string'
      || !Number.isInteger(file.bytes) || file.bytes<0 || file.bytes>256*1024 || !/^[a-f0-9]{64}$/.test(file.sha256)) throw new Error('Invalid source file')
    const content=new TextEncoder().encode(file.text)
    if(content.byteLength!==file.bytes || await sha256(content)!==file.sha256) throw new Error('Source file digest mismatch')
    total+=file.bytes;seen.add(file.path)
  }
  if(total!==receipt.bytes)throw new Error('Source size mismatch')
  return value
}
