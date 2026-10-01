import {useEffect,useState} from 'react'
import {resolveVerifiedPreview} from './previewOrigin'

export default function useVerifiedPreview(preview,fallback,refresh=0) {
  const [result,setResult]=useState(null)
  const key=preview?`${preview.siteId}/${preview.sha256}/${preview.entrySha256}/${preview.url}/${preview.port}/${preview.files}/${preview.bytes}/${refresh}`:''
  useEffect(()=>{
    if(!preview || !fallback)return
    let current=true
    const controller=new AbortController()
    const timer=setTimeout(()=>controller.abort(),5000)
    resolveVerifiedPreview(preview,fallback,controller.signal)
      .then(access=>{if(current)setResult({key,access})})
      .catch(()=>{if(current)setResult({key,access:{...fallback,frameUrl:null,unavailable:true}})})
      .finally(()=>clearTimeout(timer))
    return()=>{current=false;controller.abort();clearTimeout(timer)}
    // The receipt and refresh key, not an ephemeral access object, own the probe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[key])
  return result?.key===key?result.access:{...fallback,frameUrl:null,checking:true}
}
