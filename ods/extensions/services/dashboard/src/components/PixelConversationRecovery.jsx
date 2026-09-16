import {useState} from 'react'
import {downloadConversation} from '../lib/pixelConversationExport'

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
  return <div className="px-6 py-2 text-sm text-amber-300">
    <p role="alert">{error}</p>
    <button type="button" className="my-1 rounded border border-current px-3 py-1" onClick={download}>Download recovery copy</button>
    <p className="text-xs">Includes current message text and draft. Active work continues. Large copies may exceed conversation import limits; the JSON file still preserves the text.</p>
    {downloadError && <p role="alert">{downloadError}</p>}
  </div>
}
