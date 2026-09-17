import './portal-agent-experience.css'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import PortalInlineCitation from './PortalInlineCitation'
import {useMemo,useLayoutEffect} from 'react'
import {useResponseReveal} from '../lib/useResponseReveal'

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
const PLUGINS=[rehypeHighlight,revealNewText]
export default function PortalStreamingText({children, active=false, animate=active, instant=false, onReveal, components=EMPTY_COMPONENTS}) {
  const fullSource=String(children ?? '')
  const source=useResponseReveal(fullSource,{animate,instant})
  const revealing=source!==fullSource
  useLayoutEffect(()=>{onReveal?.()},[source,onReveal])
  const renderers=useMemo(()=>({...components,a:PortalInlineCitation,span:StreamSpan}),[components])
  return <div className={`portal-streaming-text ${active || revealing?'is-streaming':''}`} aria-busy={active || revealing}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={PLUGINS} components={renderers}>{source}</ReactMarkdown>
    {(active || revealing) && <span className="portal-stream-cursor" aria-hidden="true"/>}
  </div>
}
