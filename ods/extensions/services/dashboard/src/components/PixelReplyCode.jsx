import {useEffect, useRef, useState} from 'react'

const textOf = node => node?.type === 'text' ? node.value : (node?.children || []).map(textOf).join('')

/** Markdown reply snippets are model output, not verified workspace artifacts. */
export default function PixelReplyCode({children, node}) {
  const text = textOf(node)
  const pending = useRef(null)
  const [status, setStatus] = useState('')
  useEffect(() => {
    setStatus('')
    return () => {clearTimeout(pending.current?.timer); pending.current = null}
  }, [text])
  async function copy() {
    if (pending.current) return
    const request = {timer:null}
    pending.current = request
    setStatus('copying')
    try {
      await Promise.race([
        navigator.clipboard.writeText(text),
        new Promise((_, reject) => {request.timer = setTimeout(() => reject(new Error('Clipboard timed out')), 5000)}),
      ])
      if (pending.current === request) setStatus('copied')
    } catch {if (pending.current === request) setStatus('error')}
    finally {
      clearTimeout(request.timer)
      if (pending.current === request) pending.current = null
    }
  }
  function download() {
    clearTimeout(pending.current?.timer)
    pending.current = null
    let url, link
    try {
      url = URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}))
      link = document.createElement('a')
      link.href = url
      link.download = 'ods-reply-snippet.txt'
      document.body.append(link)
      link.click()
      setStatus('saved')
    } catch {setStatus('download-error')}
    finally {
      link?.remove()
      if (url) setTimeout(() => URL.revokeObjectURL(url), 1000)
    }
  }
  return <div className="my-2 overflow-hidden rounded border border-theme-border bg-theme-bg/70">
    <div className="flex flex-wrap items-center gap-3 border-b border-theme-border px-2 py-1 text-xs">
      <button type="button" disabled={status === 'copying'} onClick={copy}>{status === 'copying' ? 'Copying snippet…' : 'Copy snippet'}</button>
      <button type="button" onClick={download}>Download snippet</button>
      {status === 'copied' && <span role="status">Snippet copied</span>}
      {status === 'saved' && <span role="status">Text download started</span>}
      {status === 'error' && <span role="alert">Clipboard unavailable. Select the code or download it instead.</span>}
      {status === 'download-error' && <span role="alert">Download could not start. Select the code or try again.</span>}
    </div>
    <pre tabIndex={0} aria-label="Reply code snippet" className="overflow-x-auto [&>code]:block [&>code]:p-2">{children}</pre>
  </div>
}
