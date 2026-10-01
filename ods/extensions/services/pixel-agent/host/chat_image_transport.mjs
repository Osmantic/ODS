// Edge validates original text against history before adding delivery guidance.
// Ingress verifies image identities again before persisting native-session copies.
import {createHash} from 'node:crypto';
import {CHAT_IMAGE_BYTES, ChatImageError, normalizeChatImageReference} from './chat_image_store.mjs';

const fail=()=>{throw new ChatImageError('invalid-image-turn',400);};
export function validateImageRoute(route, model) {
  if(!route || Object.keys(route).sort().join()!=='routeFingerprint,unknownConsent'
      || typeof route.routeFingerprint!=='string' || route.routeFingerprint.length!==64
      || !/^[a-f0-9]{64}$/.test(route.routeFingerprint) || typeof route.unknownConsent!=='boolean') fail();
  if(!model || (model.imageRouteFingerprint??model.routeFingerprint)!==route.routeFingerprint) throw new ChatImageError('image-route-changed',409);
  if(model.imageInput==='unsupported') throw new ChatImageError('image-input-unsupported',409);
  if(model.imageInput!=='supported' && !(model.imageInput==='unknown' && route.unknownConsent))
    throw new ChatImageError('image-input-unverified',409);
  return {imageInput:model.imageInput,routeFingerprint:route.routeFingerprint,unknownConsent:route.unknownConsent};
}
export function decodeChatImageTurn(message, archived) {
  if(!archived?.images) return [];
  if(message?.role!=='user' || !Array.isArray(message.images) || !Array.isArray(message.content)
      || !Array.isArray(archived.images) || !archived.images.length || archived.images.length>4
      || message.images.length!==archived.images.length) fail();
  const references=archived.images.map(normalizeChatImageReference);
  const submitted=message.images.map(normalizeChatImageReference);
  if(submitted.some((item,index)=>item.id!==references[index].id || item.sha256!==references[index].sha256)) fail();
  if(new Set(references.map(item=>item.id)).size!==references.length) fail();
  const parts=[];
  for(const part of message.content) {
    if(part?.type==='text' && typeof part.text==='string' && Object.keys(part).sort().join()==='text,type') continue;
    if(!part || Object.keys(part).sort().join()!=='image_url,type' || part.type!=='image_url'
        || !part.image_url || Object.keys(part.image_url).join()!=='url' || typeof part.image_url.url!=='string') fail();
    parts.push(part);
  }
  if(parts.length!==references.length) fail();
  let total=0;
  return parts.map((part,index)=>{
    const value=part.image_url.url;
    if(value.length>4*Math.ceil(CHAT_IMAGE_BYTES/3)+32) fail();
    const match=/^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})$/.exec(value);
    if(!match || match[0].length!==value.length) fail();
    const data=Buffer.from(match[2],'base64');
    total+=data.length;
    if(!data.length || total>CHAT_IMAGE_BYTES || data.toString('base64')!==match[2]
        || createHash('sha256').update(data).digest('hex')!==references[index].sha256) fail();
    return {...references[index],mimeType:match[1],data};
  });
}

export function nativeHistoryMessages(messages) {
  return messages.map(message=>{
    if(!message.images) return {role:message.role,content:message.content};
    const references=message.images.map(normalizeChatImageReference);
    // A reference is retrieval metadata, never a claim of visual perception.
    return {role:message.role,content:message.content+'\n\n[Private conversation image references; '
      +'use pixel_ods_image_read to inspect original bytes if needed. These identifiers do not describe image contents.]\n'
      +JSON.stringify(references)};
  });
}
