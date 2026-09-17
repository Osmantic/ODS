import {useState} from 'react'
import {Info, Download} from 'lucide-react'
import {downloadConversation} from '../lib/pixelConversationExport'
import './portal-agent-experience.css'

export default function PixelConversationRecovery({error,chatId,messages,draft}) {
  const [downloadError,setDownloadError] = useState('')
  function download() {
    // Capture live text at click time, without depending on successful storage
    // or exporting task/publication metadata as recoverable execution authority.
    const conversation = {schema:1,chatId,draft,messages:messages.map(message => ({
      role:message.role,content:message.content,
      ...(['done','error','stopped'].includes(message.status) ? {status:message.status} : {}),
    }))}
    try {downloadConversation(conversation); setDownloadError('')}
    catch {setDownloadError('Recovery download could not start. Keep this page open and copy your text manually.')}
  }
  return <div className="portal-recovery">
    <Info size={17} className="portal-recovery-icon" aria-hidden="true"/>
    <div className="portal-recovery-copy">
      <strong>Keep a copy of your changes</strong>
      <p role="alert">{error}</p>
      <details><summary>What is included?</summary><p>Current messages and your draft. Active work continues. Large copies may exceed import limits; the JSON file still preserves your text.</p></details>
      {downloadError && <p role="alert" className="portal-recovery-error">{downloadError}</p>}
    </div>
    <button type="button" onClick={download}><Download size={14} aria-hidden="true"/>Download recovery copy</button>
  </div>
}
