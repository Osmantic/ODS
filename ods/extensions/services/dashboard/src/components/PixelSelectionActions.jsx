import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const actions = [['Explain','Explain'], ['Improve','Improve'], ['Shorten','Shorten'], ['Tone','Change the tone of'], ['Grammar','Fix the grammar in']]
export default function PixelSelectionActions({ disabled, conversationId, onInsert }) {
  const [selection, setSelection] = useState(null)
  const toolbar = useRef(null)
  useEffect(() => { setSelection(null) }, [disabled, conversationId])
  useEffect(() => {
    const update = event => {
      if (event?.key === 'Escape') return
      if (disabled) return
      const value = window.getSelection()
      if (!value?.rangeCount || value.isCollapsed) { setSelection(null); return }
      const range = value.getRangeAt(0)
      const element = node => node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement
      const start = element(range.startContainer)?.closest('[data-pixel-response]')
      const end = element(range.endContainer)?.closest('[data-pixel-response]')
      const text = value.toString().trim()
      if (!start || start !== end || !text || text.length > 12000) { setSelection(null); return }
      const rect = range.getBoundingClientRect()
      setSelection({ text, x: Math.max(8, Math.min(window.innerWidth - 308, rect.left)), y: Math.min(window.innerHeight - 48, Math.max(8, rect.top - 44)) })
    }
    const outside = event => { if (!toolbar.current?.contains(event.target)) setSelection(null) }
    const escape = event => { if (event.key === 'Escape') setSelection(null) }
    const hide = () => setSelection(null)
    document.addEventListener('mouseup', update)
    document.addEventListener('keyup', update)
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => {
      document.removeEventListener('mouseup', update); document.removeEventListener('keyup', update)
      document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape)
      window.removeEventListener('scroll', hide, true); window.removeEventListener('resize', hide)
    }
  }, [disabled])
  if (!selection || disabled) return null
  return createPortal(<div ref={toolbar} className="pixel-selection-actions" role="toolbar" aria-label="Actions for selected response text" style={{left:selection.x,top:selection.y}} onPointerDown={event => event.preventDefault()}>
    {actions.map(([label, prompt]) => <button key={label} type="button" onClick={() => {
      onInsert(`${prompt} this passage:\n\n“${selection.text}”\n\n`)
      window.getSelection()?.removeAllRanges()
      setSelection(null)
    }}>{label}</button>)}
  </div>, document.body)
}
