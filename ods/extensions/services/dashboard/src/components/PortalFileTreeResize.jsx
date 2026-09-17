import {useLayoutEffect,useRef,useState} from 'react'
import './portal-file-tree-resize.css'

const clamp=(value,min,max)=>Math.max(min,Math.min(max,value))

/** Keep a user's preferred width while clamping the visible tree to its container. */
export function useFileTreeResize(container,{enabled=true,narrowBelow=600}={}) {
  const [available,setAvailable]=useState(0),[preferred,setPreferred]=useState(224)
  useLayoutEffect(()=>{
    const element=container.current
    if(!element || !enabled)return
    const measure=width=>{if(Number.isFinite(width) && width>0)setAvailable(width)}
    const read=()=>measure(element.getBoundingClientRect().width || element.clientWidth)
    read()
    if(typeof ResizeObserver==='undefined') {
      window.addEventListener('resize',read)
      return ()=>window.removeEventListener('resize',read)
    }
    const observer=new ResizeObserver(entries=>measure(entries[0]?.contentRect.width))
    observer.observe(element)
    return ()=>observer.disconnect()
  },[container,enabled])
  const minimum=180,maximum=available?Math.max(minimum,Math.floor(Math.min(560,available-280))):560
  const width=Math.round(clamp(preferred,minimum,maximum))
  const narrow=available>0 && available<narrowBelow
  return {width,minimum,maximum:Math.floor(maximum),narrow,
    style:{'--portal-file-tree-width':`${width}px`},
    resize:value=>{if(Number.isFinite(value))setPreferred(clamp(value,minimum,maximum))},
    reset:()=>setPreferred(224)}
}

/** The right pane grows when this separator moves left. Pointer capture crosses iframes. */
export default function PortalFileTreeResize({layout,label='Resize file list'}) {
  const drag=useRef(null),handle=useRef(null)
  const [dragging,setDragging]=useState(false)
  const {width,minimum,maximum,narrow,resize,reset}=layout
  function release(event) {
    if(!drag.current || (event && event.pointerId!==drag.current.pointerId))return
    const id=drag.current.pointerId
    drag.current=null;setDragging(false)
    if(handle.current?.hasPointerCapture?.(id))handle.current.releasePointerCapture(id)
  }
  useLayoutEffect(()=>{if(narrow)release()},[narrow])
  useLayoutEffect(()=>()=>{
    const id=drag.current?.pointerId
    drag.current=null
    if(id!==undefined && handle.current?.hasPointerCapture?.(id))handle.current.releasePointerCapture(id)
  },[])
  if(narrow)return null
  return <div ref={handle} className="portal-file-tree-resizer" data-dragging={dragging} role="separator"
    aria-label={label} aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={maximum}
    aria-valuenow={width} aria-valuetext={`${width} pixels`} tabIndex={0}
    onPointerDown={event=>{
      if(event.button!==0 || drag.current)return
      drag.current={pointerId:event.pointerId,x:event.clientX,width}
      event.currentTarget.setPointerCapture?.(event.pointerId)
      setDragging(true);event.preventDefault()
    }}
    onPointerMove={event=>{if(drag.current?.pointerId===event.pointerId)resize(drag.current.width+drag.current.x-event.clientX)}}
    onPointerUp={release} onPointerCancel={release} onLostPointerCapture={release}
    onDoubleClick={reset}
    onKeyDown={event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
      event.preventDefault()
      resize(event.key==='Home'?minimum:event.key==='End'?maximum:width+(event.key==='ArrowLeft'?1:-1)*(event.shiftKey?40:16))
    }}/>
}
