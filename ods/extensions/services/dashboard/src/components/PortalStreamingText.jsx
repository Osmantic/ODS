import './portal-agent-experience.css'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import PortalInlineCitation from './PortalInlineCitation'
import {useMemo,useLayoutEffect} from 'react'
import {useResponseReveal} from '../lib/useResponseReveal'
import {EMBED_NOTES} from '../lib/embedDisplay'

// Portal's own note, in its own paragraph where an embed it can't display
// was, reads as a muted aside rather than as the model's words.
function muteEmbedNotes() {
  return tree=>{
    function visit(node) {
      for(const child of node.children || []) {
        const text=child.tagName==='p' && child.children.length===1 && child.children[0]
        if(text?.type==='text' && EMBED_NOTES.includes(text.value)) child.properties={...child.properties,className:['portal-embed-note']}
        else visit(child)
      }
    }
    visit(tree)
  }
}

// Fade newly revealed text nodes without replaying completed paragraphs.
function revealNewText() {
  return tree=>{
    function visit(node,insideCode=false) {
      if(!node.children)return
      const code=insideCode || ['pre','code'].includes(node.tagName)
      node.children=node.children.flatMap(child=>{
        if(child.type!=='text' || code){visit(child,code);return [child]}
        const start=child.position?.start?.offset
        if(!Number.isInteger(start))return [child]
        return [{type:'element',tagName:'span',properties:{className:['portal-stream-reveal'],'data-stream-offset':start},children:[child]}]
      })
    }
    visit(tree)
  }
}

function StreamSpan({node,...props}) {return <span {...props}/>}
const EMPTY_COMPONENTS={}

// Inspect Markdown structure before reveal wrappers are added. Download labels
// inside code or quoted examples are not delivery instructions. This marks only
// intent; the citation renderer still requires a unique completed file receipt.
function markDocumentDownloads() {
  const text=node=>['pre','code','blockquote'].includes(node.tagName) ? '\0'
    : node.type==='text' ? node.value : (node.children || []).map(text).join('')
  return tree=>{
    function visit(node) {
      if(['pre','code','blockquote'].includes(node.tagName))return
      let before=''
      for(const child of node.children || []) {
        if(child.tagName==='a') {
          const label=text(child).trim(), href=String(child.properties?.href || '')
          const named=/^download(?:\s+(?:verified\s+)?([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]+))?\s*:?$/i.exec(label)
          const explicitLabel=named && (!named[1] || href.endsWith('/'+named[1]))
          const rawLink=label===href || label===href.split('/').at(-1)
          if(explicitLabel || (rawLink && /(?:^|\n)\s*download\s*:\s*$/i.test(before))) {
            child.properties={...child.properties,'data-ods-download-intent':true}
          }
        }
        visit(child)
        before+=text(child)
      }
    }
    visit(tree)
  }
}
const PLUGINS=[rehypeHighlight,muteEmbedNotes,markDocumentDownloads,revealNewText]
export default function PortalStreamingText({children, active=false, animate=active, instant=false, onReveal, components=EMPTY_COMPONENTS, artifacts=null}) {
  const fullSource=String(children ?? '')
  const source=useResponseReveal(fullSource,{animate,instant})
  const revealing=source!==fullSource
  useLayoutEffect(()=>{onReveal?.()},[source,onReveal])
  const renderers=useMemo(()=>({...components,a:props=><PortalInlineCitation {...props} artifacts={artifacts}/>,span:StreamSpan}),[components,artifacts])
  return <div className={`portal-streaming-text ${active || revealing?'is-streaming':''}`} aria-busy={active || revealing}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={PLUGINS} components={renderers}>{source}</ReactMarkdown>
    {(active || revealing) && <span className="portal-stream-cursor" aria-hidden="true"/>}
  </div>
}
