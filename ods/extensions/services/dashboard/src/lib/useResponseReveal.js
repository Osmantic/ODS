import {useEffect, useRef, useState} from 'react'

// Smooth real response bursts, including a single final transport frame. Never
// throttle the network, persistence, tool results or the model's lifecycle.
export function useResponseReveal(source, {animate=false, instant=false}={}) {
  const motion=useRef(null)
  if(motion.current===null)motion.current=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)') || {matches:false}
  const [reduced,setReduced]=useState(motion.current.matches)
  const [visible,setVisible]=useState(()=>animate && !instant && !motion.current.matches ? '' : source)
  const text=useRef(visible), target=useRef(source), permitted=useRef(animate)
  target.current=source
  if(animate)permitted.current=true
  const immediate=instant || reduced || !permitted.current

  useEffect(()=>{
    const query=motion.current, change=()=>setReduced(query.matches)
    query.addEventListener?.('change',change)
    return ()=>query.removeEventListener?.('change',change)
  },[])
  useEffect(()=>{
    let frame, last=0, credit=0
    const flush=()=>{text.current=target.current;setVisible(target.current)}
    if(immediate || document.visibilityState==='hidden' || text.current.startsWith(source)){flush();return}
    if(!source.startsWith(text.current))text.current=''
    const tick=now=>{
      if(document.visibilityState==='hidden'){flush();return}
      const elapsed=last ? Math.min(80,now-last) : 16
      last=now
      credit+=elapsed*Math.max(120,source.length/1.2)/1000
      const count=Math.floor(credit)
      if(count>0){
        credit-=count
        let end=Math.min(target.current.length,text.current.length+count)
        // Do not split a UTF-16 surrogate pair (emoji and non-BMP scripts).
        if(end<target.current.length && /[\uD800-\uDBFF]/.test(target.current[end-1]))end++
        text.current=target.current.slice(0,end)
        setVisible(text.current)
      }
      if(text.current.length<target.current.length)frame=requestAnimationFrame(tick)
    }
    if(text.current!==source)frame=requestAnimationFrame(tick)
    const visibility=()=>{if(document.visibilityState==='hidden'){cancelAnimationFrame(frame);flush()}}
    document.addEventListener('visibilitychange',visibility)
    return ()=>{cancelAnimationFrame(frame);document.removeEventListener('visibilitychange',visibility)}
  },[source,immediate])
  return immediate || visible.startsWith(source) ? source : (source.startsWith(visible) ? visible : '')
}
