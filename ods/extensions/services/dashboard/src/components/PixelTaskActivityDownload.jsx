import {useState} from 'react'

export default function PixelTaskActivityDownload({task}) {
  const [error, setError] = useState('')
  function download() {
    let url, link
    setError('')
    try {
      const archive = {schemaVersion:1, kind:'ods-task-activity', exportedAt:new Date().toISOString(), activity:task}
      url = URL.createObjectURL(new Blob([JSON.stringify(archive, null, 2)], {type:'application/json'}))
      link = document.createElement('a')
      link.href = url
      link.download = `ods-task-${task.runId}.json`
      document.body.append(link)
      link.click()
    } catch {
      setError('Activity download could not start. Try again after allowing browser downloads.')
    } finally {
      link?.remove()
      if (url) setTimeout(() => URL.revokeObjectURL(url), 1000)
    }
  }
  return <div className="pixel-activity-note">
    <button type="button" onClick={download}>Download activity snapshot</button>
    <p>Recorded counters and timestamps for this turn. Running turns are partial; message text and tool arguments are excluded.</p>
    {error && <p role="alert">{error}</p>}
  </div>
}
