import {useEffect,useMemo,useRef,useState} from 'react'

export default function PixelSourceExcerpt({source}) {
  const lines=useMemo(()=>source.match(/[^\n]*\n|[^\n]+$/g) || [''],[source])
  const [start,setStart]=useState('1')
  const [end,setEnd]=useState(String(lines.length))
  const [notice,setNotice]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const revision=useRef(0)
  useEffect(()=>()=>{revision.current++},[])
  const first=Number(start),last=Number(end)
  const valid=Number.isInteger(first)&&Number.isInteger(last)&&first>=1&&last>=first&&last<=lines.length
  const excerpt=valid ? lines.slice(first-1,last).join('') : ''
  const bounded=new TextEncoder().encode(excerpt).length<=65536
  function change(setter,value){revision.current++;setter(value);setNotice('');setError('');setBusy(false)}
  async function copy(){
    if(!valid||!bounded||busy)return
    const current=++revision.current
    setBusy(true);setNotice('');setError('')
    try {
      await navigator.clipboard.writeText(excerpt)
      if(current===revision.current)setNotice('Excerpt copied.')
    } catch {
      if(current===revision.current)setError('Clipboard unavailable. Select the excerpt below and copy manually.')
    } finally {if(current===revision.current)setBusy(false)}
  }
  function download(){
    if(!valid||!bounded)return
    let url,link
    revision.current++;setBusy(false);setNotice('');setError('')
    try {
      url=URL.createObjectURL(new Blob([excerpt],{type:'text/plain;charset=utf-8'}))
      link=document.createElement('a')
      link.href=url;link.download=`ods-source-lines-${first}-${last}.txt`
      document.body.append(link);link.click()
      setNotice('Excerpt download started.')
    } catch {setError('Excerpt download could not start. Select the excerpt below and copy manually.')}
    finally {link?.remove();if(url)setTimeout(()=>URL.revokeObjectURL(url),1000)}
  }
  return <details className="my-2 rounded border border-theme-border p-2">
    <summary className="cursor-pointer text-xs">Extract lines</summary>
    <p className="my-2 text-xs">Select a range from this verified file ({lines.length} lines). Excerpts are limited to 64 KiB.</p>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label>Start line<input className="ml-2 w-20 bg-theme-bg" type="number" min="1" max={lines.length} value={start} onChange={e=>change(setStart,e.target.value)}/></label>
      <label>End line<input className="ml-2 w-20 bg-theme-bg" type="number" min="1" max={lines.length} value={end} onChange={e=>change(setEnd,e.target.value)}/></label>
      <button type="button" disabled={!valid||!bounded||busy} onClick={copy}>{busy?'Copying excerpt...':'Copy excerpt'}</button>
      <button type="button" disabled={!valid||!bounded} onClick={download}>Download excerpt</button>
    </div>
    {!valid&&<p role="alert">Choose an inclusive range between 1 and {lines.length}.</p>}
    {valid&&!bounded&&<p role="alert">Choose fewer lines to keep the excerpt within 64 KiB.</p>}
    {valid&&bounded&&<textarea aria-label="Selected source excerpt" className="my-2 h-32 w-full bg-theme-bg p-2 font-mono text-xs" readOnly value={excerpt} onFocus={e=>e.target.select()}/>}
    {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
  </details>
}
