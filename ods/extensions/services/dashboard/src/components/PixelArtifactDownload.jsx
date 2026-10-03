import { useEffect, useRef, useState } from 'react'
import { loadArtifactBytes } from '../lib/pixelArtifacts'

export default function PixelArtifactDownload({ preview, file }) {
  const [state, setState] = useState('idle')
  const pending = useRef(null)
  useEffect(() => () => { pending.current?.abort(); pending.current = null }, [])

  async function download() {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setState('loading')
    let timeout
    try {
      const bytes = await Promise.race([
        loadArtifactBytes(preview, file, controller.signal),
        new Promise((_, reject) => {
          timeout = setTimeout(() => {
            controller.abort()
            reject(new Error('Artifact verification timed out'))
          }, 12000)
        }),
      ])
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }))
      let link
      try {
        link = document.createElement('a')
        link.href = url
        link.download = file.path.split('/').at(-1)
        document.body.append(link)
        link.click()
      } finally {
        link?.remove()
        // Give the browser a task to consume the URL before releasing it.
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
      setState('saved')
    } catch {
      if (pending.current === controller) setState('error')
    } finally {
      clearTimeout(timeout)
      if (pending.current === controller) pending.current = null
    }
  }
  return <span>
    <button type="button" onClick={download} disabled={state === 'loading'} aria-label={`Download ${file.path}`}>{state === 'loading' ? 'Verifying download…' : 'Download'}</button>
    {state === 'saved' && <small role="status">Verified download started</small>}
    {state === 'error' && <small role="alert">Download could not be verified. Try again.</small>}
  </span>
}
