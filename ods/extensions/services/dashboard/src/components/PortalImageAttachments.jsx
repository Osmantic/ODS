import {useRef, useState} from 'react'
import {ImagePlus, Loader2, RotateCw, X} from 'lucide-react'
import {imageUrl, imageRouteIdentity} from '../lib/pixelImages'
import './PortalImageAttachments.css'

export function PortalImagePicker({disabled,onChoose}) {
  const field=useRef(null)
  return <>
    <input ref={field} hidden multiple type="file" accept="image/png,image/jpeg,image/webp" aria-label="Choose images" disabled={disabled} onChange={event=>{onChoose(event.target.files);event.target.value=''}}/>
    <button type="button" title="Add images" aria-label="Add images" disabled={disabled} onClick={()=>field.current?.click()}><ImagePlus size={16}/></button>
  </>
}
export function PortalConversationImages({chatId,images}) {
  const [failed,setFailed]=useState({})
  if(!images?.length)return null
  return <div className="portal-conversation-images" aria-label="Conversation images">
    {images.map((image,index)=><figure key={image.id}>
      {failed[image.id]?<span>Image unavailable</span>:<img src={imageUrl(chatId,image.id)} alt={`Attached image ${index+1}`} loading="lazy" referrerPolicy="no-referrer" onError={()=>setFailed(value=>({...value,[image.id]:true}))}/>}
    </figure>)}
  </div>
}
export default function PortalImageAttachments({attachments,chatId,disabled,hasHistory,model,consented,onConsent,onRefresh}) {
  const needed=attachments.items.length>0 || hasHistory
  const policy=model?.imageInput, verified=Boolean(imageRouteIdentity(model))
  return <div className="portal-image-attachments">
    {attachments.items.length>0 && <ul aria-label="Attached images">
      {attachments.items.map((item,index)=><li key={item.key}>
        <img src={item.preview || imageUrl(chatId,item.receipt?.id)} alt={`Image attachment ${index+1}`}/>
        <div className="portal-image-attachment-copy"><strong>{item.file?.name || `Image ${index+1}`}</strong>
          <small>{item.status==='uploading'?'Saving privately…':item.status==='removing'?'Removing attachment…':item.status==='failed'?'Upload not confirmed':`${item.receipt.width} × ${item.receipt.height} · Ready`}</small>
          {item.error && <span role="alert">{item.error}</span>}
        </div>
        {item.status==='uploading' && <Loader2 size={14} className="animate-spin" aria-label="Uploading image"/>}
        {item.status==='failed' && <button type="button" disabled={disabled} aria-label={`Retry image ${index+1}`} onClick={()=>attachments.retry(item)}><RotateCw size={14}/></button>}
        <button type="button" disabled={disabled || item.status==='removing'} aria-label={`Remove image ${index+1}`} onClick={()=>attachments.remove(item.key)}><X size={14}/></button>
      </li>)}
    </ul>}
    {attachments.error && <p role="alert">{attachments.error}</p>}
    {needed && <div className="portal-image-route" role="group" aria-label="Image model capability">
      {!verified?<><span>The current model route is not verified. Your attachments stay in the draft.</span><button type="button" disabled={disabled} onClick={onRefresh}>Refresh model status</button></>
        :policy==='unsupported'?<span>This model is declared text-only. Choose an image-capable model; Portal will keep your draft.</span>
        :policy==='supported'?<span>Images will be sent to the selected model when you send this message.</span>
        :<label><input type="checkbox" checked={consented} disabled={disabled} onChange={event=>onConsent(event.target.checked)}/><span>Image support is unknown for this model. Allow an image test on this route.</span></label>}
    </div>}
    {attachments.items.length>0 && <p className="portal-image-private-note">Stored privately in this conversation · Up to 4 images, 8 MiB combined. Unsent uploads may expire after 7 days without use.</p>}
  </div>
}
