import { useEffect, useRef, useState } from 'react'
import { loadArtifactBytes } from '../lib/pixelArtifacts'
import { PixelCodeLines, PixelLanguageBadge } from './PixelCodeBlock'
import PixelArtifactDownload from './PixelArtifactDownload'
import PixelSourceFind from './PixelSourceFind'


const TEXT_LANGUAGES = {html:'html',htm:'html',css:'css',scss:'scss',js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',py:'python',sh:'bash',yml:'yaml',yaml:'yaml',toml:'ini',json:'json',svg:'xml',xml:'xml',md:'markdown',markdown:'markdown',txt:'text',map:'json',csv:'text',tsv:'text'}

export default function PixelPreviewSource({ preview, file }) {
  const path = file?.path || 'index.html'
  const expectedDigest = file?.sha256 || preview.entrySha256
  const extension = path.split('.').pop().toLowerCase()
  const language = TEXT_LANGUAGES[extension]
  const [source, setSource] = useState(null)
  const [binarySize, setBinarySize] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const codeRef = useRef(null)
  useEffect(() => {
    let current = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 12000)
    setSource(null); setBinarySize(null); setError(''); setCopied(false)
    async function load() {
      try {
        const bytes = await loadArtifactBytes(preview, {path, sha256:expectedDigest, bytes:file?.bytes}, controller.signal)
        if (language) {
          const value = new TextDecoder('utf-8', {fatal:true}).decode(bytes)
          if (current) setSource(value)
        } else if (current) setBinarySize(bytes.byteLength)
      } catch { if (current) setError('The published source could not be verified. No unverified code is displayed.') }
      finally { clearTimeout(timeout) }
    }
    void load()
    return () => { current = false; controller.abort(); clearTimeout(timeout) }
  }, [preview.siteId, path, expectedDigest, file?.bytes, language, attempt])
  async function copy() {
    try { await navigator.clipboard.writeText(source); setCopied(true); setError('') }
    catch { setCopied(false); setError('Clipboard access failed. You can select and copy the code manually.') }
  }
  return <section className="pixel-preview-source pixel-original-source" aria-label={path === 'index.html' ? 'Published HTML source' : `Source: ${path}`}>
    {source === null && binarySize === null && !error && <p role="status">Verifying source…</p>}
    {binarySize !== null && <p role="status">Binary asset. Its bytes are verified; no text source is available.</p>}
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Retry source</button></div>}
    <div className="pixel-code-block">
      <header className="code-block-header"><PixelLanguageBadge path={path}/><span title={path}>{path}</span><PixelArtifactDownload key={`${preview.siteId}/${path}/${expectedDigest}`} preview={preview} file={{path, sha256:expectedDigest, bytes:file?.bytes}}/>{language && <button type="button" aria-label="Copy code" onClick={copy} disabled={source === null}>{copied ? 'Copied' : 'Copy'}</button>}</header>
      {source !== null && <><PixelSourceFind key={`${preview.siteId}/${path}/${expectedDigest}`} source={source} codeRef={codeRef}/><pre ref={codeRef} tabIndex={0} aria-label={`Code for ${path}`}><PixelCodeLines source={source} language={language}/></pre></>}

    </div>
    {(source !== null || binarySize !== null) && <p className="pixel-source-verification">Published snapshot · SHA-256 verified{binarySize !== null ? ` · ${binarySize.toLocaleString()} bytes` : ''}</p>}
  </section>
}
