import {useEffect, useState} from 'react'
export const SEND_KEY_STORAGE = 'ods.pixel.send-key.v1'
function read() {
  try {return localStorage.getItem(SEND_KEY_STORAGE) === 'mod-enter' ? 'mod-enter' : 'enter'}
  catch {return 'enter'}
}
export function shouldSendMessage(event, mode) {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.nativeEvent?.isComposing || event.isComposing || event.keyCode === 229) return false
  return mode === 'enter' || Boolean(event.ctrlKey || event.metaKey)
}
export function usePixelSendKey() {
  const [mode,setMode]=useState(read)
  const [error,setError]=useState('')
  useEffect(()=>{
    const refresh=event=>{if(event.key===SEND_KEY_STORAGE || event.key===null){setMode(read());setError('')}}
    window.addEventListener('storage',refresh)
    return()=>window.removeEventListener('storage',refresh)
  },[])
  function change(value){
    if(!['enter','mod-enter'].includes(value))return
    setMode(value)
    try{localStorage.setItem(SEND_KEY_STORAGE,value);setError('')}
    catch{setError('This shortcut applies to this tab, but could not be saved for next time.')}
  }
  return {mode,change,error}
}
