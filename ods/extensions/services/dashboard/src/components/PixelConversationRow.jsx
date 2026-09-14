import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Archive, Download, Pencil, Pin, X} from 'lucide-react'
import PixelConversationOrganizer from './PixelConversationOrganizer'
import {conversationLabels, saveConversationLabels} from '../lib/pixelConversationLabels'

export function ConversationTitle({title}) {
  const viewport=useRef(null), text=useRef(null)
  const [overflow,setOverflow]=useState(0)
  useLayoutEffect(()=>{
    const measure=()=>setOverflow(Math.max(0,(text.current?.scrollWidth || 0)-(viewport.current?.clientWidth || 0)))
    measure()
    const observer=typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    observer?.observe(viewport.current)
    observer?.observe(text.current)
    window.addEventListener('resize',measure)
    return ()=>{observer?.disconnect();window.removeEventListener('resize',measure)}
  },[title])
  return <strong ref={viewport} className="conversation-title" data-overflow={overflow>0} style={{'--title-travel':`${-overflow}px`,'--title-duration':`${Math.max(4,overflow/28+2)}s`}}><span ref={text}>{title}</span></strong>
}

export default function PixelConversationRow({chat,title,onDelete,onExport,onSaved,children}) {
  const row=useRef(null), menu=useRef(null), returnFocus=useRef(null)
  const [position,setPosition]=useState(null)
  const [error,setError]=useState('')
  const labels=conversationLabels(chat.chatId)
  function close(restore=true) {setPosition(null);setError('');if(restore) returnFocus.current?.focus()}
  function open(event) {
    if(event.target.closest('dialog')) return
    event.preventDefault()
    returnFocus.current=row.current.querySelector('.conversation-link')
    const box=row.current.getBoundingClientRect()
    setPosition({x:event.clientX || box.left+24,y:event.clientY || box.bottom})
  }
  useLayoutEffect(()=>{
    if(!position || !menu.current) return
    const box=menu.current.getBoundingClientRect()
    menu.current.style.left=`${Math.max(8,Math.min(position.x,window.innerWidth-box.width-8))}px`
    menu.current.style.top=`${Math.max(8,Math.min(position.y,window.innerHeight-box.height-8))}px`
    menu.current.querySelector('[role="menuitem"]')?.focus()
  },[position])
  useEffect(()=>{
    if(!position) return
    const outside=event=>{if(!menu.current?.contains(event.target)) close(false)}
    const dismiss=()=>close(false)
    document.addEventListener('pointerdown',outside)
    window.addEventListener('resize',dismiss)
    window.addEventListener('scroll',dismiss,true)
    return ()=>{document.removeEventListener('pointerdown',outside);window.removeEventListener('resize',dismiss);window.removeEventListener('scroll',dismiss,true)}
  },[position])
  function toggle(field) {
    try {saveConversationLabels(chat.chatId,{...labels,[field]:!labels[field]},labels);close();onSaved?.()}
    catch(failure) {setError(failure.message)}
  }
  return <div ref={row} className="conversation-row" onContextMenu={open} onKeyDown={event=>{
    if(event.key==='ContextMenu' || (event.shiftKey && event.key==='F10')) open(event)
  }}>
    {children}
    <PixelConversationOrganizer chat={chat} title={title} onSaved={onSaved}/>
    <button className="conversation-delete" aria-label={`Delete chat: ${title}`} title="Delete chat" onClick={onDelete}><X size={13}/></button>
    {position && createPortal(<div ref={menu} role="menu" aria-label={`Chat actions: ${title}`} className="conversation-context-menu" style={{left:position.x,top:position.y}} onContextMenu={event=>event.preventDefault()} onKeyDown={event=>{
      if(event.key==='Escape') {event.preventDefault();close();return}
      if(event.key==='Tab') {close();return}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return
      event.preventDefault()
      const items=[...event.currentTarget.querySelectorAll('[role="menuitem"]')]
      const index=items.indexOf(document.activeElement)
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length
      items[next]?.focus()
    }}>
      <button role="menuitem" onClick={()=>{close(false);row.current.querySelector('.conversation-organize')?.click()}}><Pencil size={15}/>Rename</button>
      <button role="menuitem" onClick={()=>toggle('pinned')}><Pin size={15}/>{labels.pinned?'Unpin':'Pin'}</button>
      <button role="menuitem" onClick={()=>toggle('archived')}><Archive size={15}/>{labels.archived?'Unarchive':'Archive'}</button>
      <div role="separator"/>
      <button role="menuitem" onClick={()=>{close();onExport()}}><Download size={15}/>Export conversation</button>
      <button role="menuitem" onClick={()=>{close(false);row.current.querySelector('.conversation-delete')?.click()}}><X size={15}/>Delete chat</button>
      {error && <p role="alert">{error}</p>}
    </div>,document.body)}
  </div>
}
