import {useState} from 'react'
import { ScanEye } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './pixel-draft-preview.css'

const components = {
  a:({children, href}) => <span className="underline" title={href}>{children}</span>,
  img:({alt}) => <span>[Image: {alt || 'untitled'}]</span>,
  input:({checked}) => <input type="checkbox" checked={Boolean(checked)} readOnly disabled/>,
}
export default function PixelDraftPreview({input}) {
  const [open,setOpen]=useState(false)
  return <div className="pixel-draft-preview">
    <button type="button" title={open?'Hide draft preview':'Preview draft'} aria-label={open?'Hide draft preview':'Preview draft'} disabled={!input.trim() && !open} aria-expanded={open} onClick={()=>setOpen(value=>!value)}><ScanEye size={16}/></button>
    {open && <section aria-label="Draft Markdown preview">
      <p className="pixel-draft-note">Preview only. Links and external images stay inactive.</p>
      {input.trim() ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{input}</ReactMarkdown> : <p>Your draft is empty.</p>}
    </section>}
  </div>
}
