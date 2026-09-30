import {useEffect,useRef,useState} from 'react'
import {Copy,Download,FileCode2} from 'lucide-react'
import {loadSourceReview} from '../lib/pixelSourceReview'
import PortalFileTree from './PortalFileTree'
import {PixelCodeLines,fileLanguage} from './PixelCodeBlock'

export default function PortalSourceReview({preview,refresh=0}) {
  const key=`${preview.siteId}/${preview.source?.sourceId}`
  const [state,setState]=useState(null),[selected,setSelected]=useState(null),[retry,setRetry]=useState(0),[notice,setNotice]=useState('')
  const currentSelection=useRef(null)
  const snapshot=state?.key===key?state:null
  currentSelection.current=`${key}/${selected || ''}`
  useEffect(()=>{
    let current=true;const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000)
    setState(null);setSelected(null);setNotice('')
    loadSourceReview(preview,abort.signal).then(value=>{if(current)setState({key,value})})
      .catch(()=>{if(current)setState({key,error:true})}).finally(()=>clearTimeout(timer))
    return()=>{current=false;abort.abort();clearTimeout(timer)}
  },[key,preview.source?.sha256,refresh,retry])
  if(snapshot?.error)return <p className="portal-source-notice" role="alert">Project source could not be verified. <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry source files</button></p>
  if(!snapshot?.value)return <p className="portal-source-notice" role="status">Verifying project source…</p>
  const source=snapshot.value,file=source.files.find(item=>item.path===selected)||source.files[0]
  const omitted=Object.values(source.omitted).reduce((sum,count)=>sum+count,0)
  async function copy(){
    const selection=currentSelection.current
    try{await navigator.clipboard.writeText(file.text);if(currentSelection.current===selection)setNotice('Source copied')}
    catch{if(currentSelection.current===selection)setNotice('Copy failed. Select the source text to copy it.')}
  }
  function download(){
    const url=URL.createObjectURL(new Blob([new TextEncoder().encode(file.text)],{type:'text/plain;charset=utf-8'}))
    const anchor=document.createElement('a');anchor.href=url;anchor.download=file.path.split('/').at(-1);anchor.click()
    setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return <section className="portal-source-review" aria-label="Verified project source">
    <p className="portal-source-notice">{source.files.length} source files captured at publication · SHA-256 verified. This snapshot does not prove which bytes produced the build.</p>
    <p className="portal-source-notice">Eligible text files only. Dependencies, generated folders, hidden files and sensitive files are excluded.{omitted>0?` Omitted: ${source.omitted.directories} directories, ${source.omitted.files} files or hidden entries, ${source.omitted.sensitiveFiles} sensitive files.`:''}</p>
    <div className="portal-source-review-layout">
      <aside><PortalFileTree files={source.files} rootPath={source.relativeDirectory} selectedPath={file.path} onSelectFile={path=>{setSelected(path);setNotice('')}} label="Project source files" filterLabel="Filter source files"/></aside>
      <div className="portal-source-review-code">
        <header><span><FileCode2 size={14}/> {file.path}</span><button type="button" onClick={copy} aria-label="Copy complete source"><Copy size={14}/></button><button type="button" onClick={download} aria-label="Download source file"><Download size={14}/></button></header>
        {notice && <p role="status">{notice}</p>}
        <pre tabIndex={0} aria-label={`Source code for ${file.path}`}><PixelCodeLines source={file.text} language={fileLanguage(file.path)}/></pre>
      </div>
    </div>
  </section>
}
