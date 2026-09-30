import {useCallback, useEffect, useRef, useState} from 'react'
import {discardPortalImage, draftImageReceipts, IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_IMAGES, uploadPortalImage} from '../lib/pixelImages'

export default function usePortalImages(chatId, initial = []) {
  const [state,setState] = useState(() => ({chatId,items:draftImageReceipts(initial).map(receipt=>({key:receipt.id,receipt,status:'ready'})),error:''}))
  const current = useRef(state), scope = useRef(chatId), jobs = useRef(new Map()), counter = useRef(0)
  current.current = state; scope.current = chatId
  const update = useCallback(fn => setState(previous => {
    const next = fn(previous); current.current=next; return next
  }),[])
  const dispose = useCallback(() => {
    jobs.current.forEach(job=>job.abort());jobs.current.clear()
    for (const item of current.current.items) if (item.preview) URL.revokeObjectURL(item.preview)
  },[])
  useEffect(() => () => dispose(),[dispose])
  const replace = useCallback((nextChat, receipts=[]) => {
    dispose();scope.current=nextChat
    const next={chatId:nextChat,items:draftImageReceipts(receipts).map(receipt=>({key:receipt.id,receipt,status:'ready'})),error:''}
    current.current=next;setState(next)
  },[dispose])
  const remove = useCallback(async key => {
    const at=scope.current, selected=current.current.items.find(item=>item.key===key)
    if(selected?.receipt) {
      try {await discardPortalImage(at,selected.receipt.id)}
      catch(error) {if(scope.current===at)update(previous=>({...previous,error:error.message}));return}
      if(scope.current!==at)return
    }
    jobs.current.get(key)?.abort();jobs.current.delete(key)
    update(previous=>({...previous,error:'',items:previous.items.filter(item=>{
      if(item.key!==key)return true
      if(item.preview)URL.revokeObjectURL(item.preview)
      return false
    })}))
  },[update])
  const run = useCallback(async item => {
    const at=scope.current, controller=new AbortController()
    jobs.current.set(item.key,controller)
    update(previous=>({...previous,error:'',items:previous.items.map(value=>value.key===item.key?{...value,status:'uploading',error:''}:value)}))
    const timer=setTimeout(()=>controller.abort(),45000)
    try {
      const receipt=await uploadPortalImage(at,item.file,controller.signal)
      if(controller.signal.aborted || scope.current!==at || jobs.current.get(item.key)!==controller)return
      update(previous=>{
        if(previous.items.some(value=>value.key!==item.key && value.receipt?.id===receipt.id)) {
          if(item.preview)URL.revokeObjectURL(item.preview)
          return {...previous,error:'That image is already attached.',items:previous.items.filter(value=>value.key!==item.key)}
        }
        return {...previous,items:previous.items.map(value=>value.key===item.key?{...value,receipt,status:'ready',error:''}:value)}
      })
    } catch(error) {
      if(scope.current!==at || jobs.current.get(item.key)!==controller)return
      update(previous=>({...previous,items:previous.items.map(value=>value.key===item.key?{...value,status:'failed',error:error?.name==='AbortError'?'Upload timed out. Retry or remove this image.':error.message}:value)}))
    } finally {clearTimeout(timer);if(jobs.current.get(item.key)===controller)jobs.current.delete(item.key)}
  },[update])
  const choose = useCallback(files => {
    const selected=Array.from(files || [])
    if(!selected.length)return
    const previous=current.current
    const error=selected.some(file=>!IMAGE_TYPES.includes(file.type) || !file.size)?'Choose nonempty PNG, JPEG or WebP images.'
      :previous.items.length+selected.length>MAX_IMAGES?'Attach up to four images per message.'
      :previous.items.reduce((sum,item)=>sum+(item.file?.size ?? item.receipt?.bytes ?? 0),0)+selected.reduce((sum,file)=>sum+file.size,0)>MAX_IMAGE_BYTES?'The images exceed the combined 8 MiB limit. Nothing was added.':''
    if(error){update(value=>({...value,error}));return}
    const added=selected.map(file=>({key:`local-${++counter.current}`,file,preview:URL.createObjectURL(file),status:'uploading'}))
    const next={...previous,error:'',items:[...previous.items,...added]}
    current.current=next;setState(next)
    added.forEach(item=>void run(item))
  },[run,update])
  const items=state.chatId===chatId?state.items:[]
  return {items,error:state.chatId===chatId?state.error:'',choose,remove,retry:run,replace,
    clear:()=>replace(chatId),receipts:items.filter(item=>item.status==='ready').map(item=>item.receipt),
    busy:items.some(item=>item.status!=='ready')}
}
