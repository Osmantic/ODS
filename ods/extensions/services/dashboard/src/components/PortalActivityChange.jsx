import {useMemo,useState} from 'react'
import {Copy,Check} from 'lucide-react'
import {PixelCodeLines,fileLanguage} from './PixelCodeBlock'
import {PixelChangeCounts} from './PixelFileChanges'

function lines(text) {return text?text.replace(/\n$/,'').split('\n'):[]}
export function activityChangeRows(change) {
  if(change.kind==='patch') {
    let inHunk=false
    return lines(change.after).filter(line=>{
      if(line.startsWith('***')){inHunk=false;return false}
      if(line.startsWith('@@')){inHunk=true;return false}
      return inHunk || (!line.startsWith('--- ') && !line.startsWith('+++ '))
    }).slice(0,200).map(line=>({type:line.startsWith('+')?'add':line.startsWith('-')?'remove':'context',text:/^[+ -]/.test(line)?line.slice(1):line}))
  }
  const before=lines(change.before).slice(0,200),after=lines(change.after).slice(0,200)
  // Bounded line LCS preserves unchanged lines inside a replacement.
  const dp=Array.from({length:before.length+1},()=>new Uint16Array(after.length+1))
  for(let i=before.length-1;i>=0;i--)for(let j=after.length-1;j>=0;j--)dp[i][j]=before[i]===after[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1])
  const rows=[];let i=0,j=0
  while(i<before.length || j<after.length) {
    if(i<before.length && j<after.length && before[i]===after[j]){rows.push({type:'context',text:before[i++]});j++}
    else if(i<before.length && (j===after.length || dp[i+1][j]>=dp[i][j+1]))rows.push({type:'remove',text:before[i++]})
    else rows.push({type:'add',text:after[j++]})
  }
  return rows
}
export function ActivityChangeCounts({change}) {
  const rows=useMemo(()=>change?activityChangeRows(change):[],[change])
  if(!change || change.truncated || change.kind==='write' || lines(change.before).length>200 || lines(change.after).length>200)return null
  return <PixelChangeCounts additions={rows.filter(row=>row.type==='add').length} deletions={rows.filter(row=>row.type==='remove').length}/>
}
export default function PortalActivityChange({change}) {
  const rows=useMemo(()=>activityChangeRows(change),[change]),[copied,setCopied]=useState('')
  const truncated=change.truncated || lines(change.before).length>200 || lines(change.after).length>200
  const patch=rows.map(row=>`${row.type==='add'?'+':row.type==='remove'?'-':' '}${row.text}`).join('\n')
  return <div className="portal-agent-change">
    <header><span>{change.file}</span>{!truncated && change.kind!=='write' && <PixelChangeCounts additions={rows.filter(r=>r.type==='add').length} deletions={rows.filter(r=>r.type==='remove').length}/>}
      <button type="button" aria-label={copied || 'Copy displayed changes'} title={copied || 'Copy displayed changes'} onClick={async()=>{
        try {if(!navigator.clipboard?.writeText)throw new Error();await navigator.clipboard.writeText(patch);setCopied('Copied')}catch{setCopied('Copy failed')}
      }}>{copied==='Copied'?<Check size={13}/>:<Copy size={13}/>}</button>
    </header>
    <pre tabIndex={0} aria-label={`Changes to ${change.file}`}>{rows.length?<PixelCodeLines source={rows.map(row=>row.text).join('\n')} language={fileLanguage(change.file)} renderLine={(content,i)=><span key={i} className={`portal-agent-diff-line is-${rows[i].type}`}><span aria-hidden="true">{rows[i].type==='add'?'+':rows[i].type==='remove'?'−':' '}</span><span>{content || ' '}{i<rows.length-1?'\n':''}</span></span>}/>:<code>Empty content</code>}</pre>
    <small>{truncated?'Excerpt · larger changes are truncated':change.kind==='write'?'Written content · previous content was not supplied':'Changes reported by this tool'}</small>
    {copied==='Copy failed' && <span role="status">Copy failed. Select the displayed text to copy it.</span>}
  </div>
}
