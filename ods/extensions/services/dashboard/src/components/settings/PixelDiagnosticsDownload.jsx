import {useState} from 'react'
import {diagnosticSummary} from '../../lib/pixelDiagnosticSummary'

export default function PixelDiagnosticsDownload({results,checkedAt,busy}) {
  const [error,setError]=useState('')
  function download(){
    setError('')
    try {
      const summary=diagnosticSummary(results,checkedAt)
      const url=URL.createObjectURL(new Blob([JSON.stringify(summary,null,2)+'\n'],{type:'application/json'}))
      const link=document.createElement('a')
      link.href=url;link.download=`ods-pixel-diagnostics-${summary.checkedAt.slice(0,10)}.json`
      document.body.append(link)
      try{link.click()}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
    } catch {setError('The diagnostic summary could not be downloaded. Refresh checks and try again.')}
  }
  return <div>
    <button type="button" disabled={busy || !checkedAt} onClick={download}>Download check summary</button>
    <p className="text-xs text-theme-text-muted">Includes check outcomes, context sizes and access modes. Model names and connection details are omitted.</p>
    {error && <p role="alert">{error}</p>}
  </div>
}
