/* global FileReader, DOMException */
import {sha256} from './pixelArtifacts'

export const MAX_IMAGE_BYTES = 8 * 1024 * 1024
export const MAX_IMAGES = 4
export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const sha = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value)
const imageId = value => typeof value === 'string' && value.length === 36 && /^img-[a-f0-9]{32}$/.test(value)
const chat = value => typeof value === 'string' && value.length >= 1 && value.length <= 128 && /^[A-Za-z0-9_-]+$/.test(value)

export function normalizeImageRefs(value) {
  if (!Array.isArray(value) || !value.length || value.length > MAX_IMAGES) throw new Error('Conversation image references are invalid. No request was sent.')
  const seen = new Set()
  return value.map(item => {
    if (!item || Object.keys(item).sort().join() !== 'id,sha256' || !imageId(item.id) || !sha(item.sha256) || seen.has(item.id)) throw new Error('Conversation image references are invalid. No request was sent.')
    seen.add(item.id)
    return {id:item.id, sha256:item.sha256}
  })
}
export function messageImageRefs(message) {
  if (message.images === undefined) return {}
  if (message.role !== 'user') throw new Error('Images must belong to a user message.')
  return {images:normalizeImageRefs(message.images)}
}
export function imageUrl(chatId, id) {
  if (!chat(chatId) || !imageId(id)) return null
  return `/api/pixel/images/${encodeURIComponent(chatId)}/${id}`
}
export function imageReceipt(value) {
  if (!value || !imageId(value.id) || !sha(value.sha256) || !IMAGE_TYPES.includes(value.media_type)
      || !Number.isInteger(value.bytes) || value.bytes < 1 || value.bytes > MAX_IMAGE_BYTES
      || !Number.isInteger(value.width) || !Number.isInteger(value.height) || value.width < 1 || value.height < 1
      || Math.max(value.width,value.height) > 8192 || value.width * value.height > 16_000_000) throw new Error('The uploaded image could not be verified. Your draft is unchanged.')
  return {id:value.id, sha256:value.sha256, media_type:value.media_type, bytes:value.bytes, width:value.width, height:value.height}
}
export function draftImageReceipts(value = []) {
  if (!Array.isArray(value) || value.length > MAX_IMAGES) throw new Error('Saved image attachments are invalid.')
  const receipts = value.map(imageReceipt)
  if (new Set(receipts.map(item=>item.id)).size !== receipts.length || receipts.reduce((sum,item)=>sum+item.bytes,0) > MAX_IMAGE_BYTES) throw new Error('Saved image attachments exceed the limit.')
  return receipts
}
function fileBytes(file, signal) {
  return new Promise((resolve,reject) => {
    const reader = new FileReader()
    const abort = () => {reader.abort(); reject(new DOMException('Aborted','AbortError'))}
    const cleanup = () => signal?.removeEventListener('abort',abort)
    reader.onload = () => {cleanup(); resolve(reader.result)}
    reader.onerror = () => {cleanup(); reject(new Error('The image could not be read. Choose it again.'))}
    reader.onabort = () => {cleanup(); reject(new DOMException('Aborted','AbortError'))}
    if (signal?.aborted) {abort();return}
    signal?.addEventListener('abort',abort,{once:true})
    reader.readAsArrayBuffer(file)
  })
}
export async function uploadPortalImage(chatId, file, signal) {
  if (!chat(chatId) || !IMAGE_TYPES.includes(file.type) || !file.size || file.size > MAX_IMAGE_BYTES) throw new Error('Choose a nonempty PNG, JPEG or WebP image, up to 8 MiB.')
  const digest = await sha256(await fileBytes(file,signal))
  if (signal?.aborted) throw new DOMException('Aborted','AbortError')
  const response = await fetch(`/api/pixel/images/${encodeURIComponent(chatId)}`, {method:'POST', headers:{'Content-Type':file.type}, body:file, signal})
  let body
  try {body = await response.json()} catch {throw new Error('The image upload could not be confirmed. Retry without changing your draft.')}
  if (!response.ok) throw new Error(typeof body?.detail === 'string' && body.detail.length < 240 ? body.detail : 'The image could not be uploaded. Your draft is unchanged.')
  const receipt = imageReceipt(body)
  if (receipt.sha256 !== digest || receipt.bytes !== file.size || receipt.media_type !== file.type) throw new Error('The uploaded image differs from your selected file. Your draft is unchanged.')
  return receipt
}
export const imageRouteIdentity = model => {
  const value=model?.imageRouteFingerprint ?? model?.routeFingerprint
  return sha(value)?value:null
}
export async function discardPortalImage(chatId, id) {
  const url=imageUrl(chatId,id)
  if(!url)throw new Error('Invalid image reference.')
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000)
  try {
    const response=await fetch(url,{method:'DELETE',signal:controller.signal})
    if(!response.ok)throw new Error('The image could not be removed. Try again; your attachment is still here.')
    const receipt=await response.json()
    if(typeof receipt?.discarded!=='boolean' || typeof receipt?.retained!=='boolean'
        || receipt.discarded===receipt.retained)throw new Error('Image removal could not be confirmed. Try again.')
  } catch(error) {
    if(error?.name==='AbortError')throw new Error('Image removal timed out. Try again; your attachment is still here.')
    throw error
  } finally {clearTimeout(timer)}
}
export function imageRoute(model, consentFingerprint) {
  const routeFingerprint=imageRouteIdentity(model)
  if (!routeFingerprint) throw new Error('The current model route is not verified yet. Refresh its status before sending images; your draft is preserved.')
  if (model.imageInput === 'unsupported') throw new Error('This model is declared text-only. Choose an image-capable model to use these attachments.')
  if (model.imageInput !== 'supported' && consentFingerprint !== routeFingerprint) throw new Error('Image support is unknown for this model. Allow an image test on this route before sending.')
  return {routeFingerprint, unknownConsent:model.imageInput !== 'supported'}
}
