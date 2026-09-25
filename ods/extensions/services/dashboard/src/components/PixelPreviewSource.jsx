/* eslint-disable no-control-regex -- Reject control bytes in untrusted public input. */
import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronRight, Copy, MoreHorizontal } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { isArtifactPath, loadArtifactBytes } from '../lib/pixelArtifacts'
import { PixelCodeLines, PixelLanguageBadge, needsPlainSource } from './PixelCodeBlock'
import PixelArtifactDownload from './PixelArtifactDownload'
import PixelSourceFind from './PixelSourceFind'
import PixelSourceExcerpt from './PixelSourceExcerpt'
import './portal-workspace-source.css'


const TEXT_LANGUAGES = {html:'html',htm:'html',css:'css',scss:'scss',js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',py:'python',sh:'bash',yml:'yaml',yaml:'yaml',toml:'ini',json:'json',svg:'xml',xml:'xml',md:'markdown',markdown:'markdown',txt:'text',map:'json',csv:'text',tsv:'text'}

// Only links to this verified publication may open an internal file tab. Treat
// rendered Markdown as untrusted content: no HTML execution or remote images.
function markdownLink(href, path) {
  if (typeof href !== 'string' || !href || /[\u0000-\u0020\u007f\\]/u.test(href)) return null
  if (href.startsWith('#')) return {href}
  if (/^https?:\/\//i.test(href)) {
    try { const url = new URL(href); return url.username || url.password ? null : {href:url.href, external:true} } catch { return null }
  }
  if (/^mailto:/i.test(href)) return {href, external:true}
  if (/^[a-z][a-z\d+.-]*:/i.test(href) || href.startsWith('/')) return null
  const parts = path.split('/').slice(0,-1)
  let target
  try { target = decodeURIComponent(href.split(/[?#]/u)[0]) } catch { return null }
  if (!target || target.includes('\\')) return null
  for (const part of target.split('/')) {
    if (part === '.') continue
    if (part === '..') { if (!parts.length) return null; parts.pop() }
    else parts.push(part)
  }
  const resolved = parts.join('/')
  return isArtifactPath(resolved) ? {path:resolved} : null
}

const headingAnchor = value => value.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/gu, '-')
function documentHeadings({prefix}) {
  return tree => {
    const counts = new Map()
    const text = node => node.type === 'text' ? node.value : (node.children || []).map(text).join('')
    function visit(node) {
      if (/^h[1-6]$/u.test(node.tagName || '')) {
        const anchor = headingAnchor(text(node))
        const count = counts.get(anchor) || 0
        counts.set(anchor, count + 1)
        node.properties = {...node.properties, id:`${prefix}${anchor}${count ? `-${count}` : ''}`}
      }
      node.children?.forEach(visit)
    }
    visit(tree)
  }
}

function MarkdownLink({href, path, anchorPrefix, onOpenFile, children}) {
  const target = markdownLink(href, path)
  if (!target || (target.path && !onOpenFile)) return <span>{children}</span>
  if (target.path) return <button type="button" className="portal-source-file-link" onClick={() => onOpenFile(target.path)}>{children}</button>
  let url = target.href
  if (url.startsWith('#')) {
    try { url = `#${anchorPrefix}${decodeURIComponent(url.slice(1))}` } catch { return <span>{children}</span> }
  }
  return <a href={url} target={target.external ? '_blank' : undefined} rel={target.external ? 'noopener noreferrer' : undefined}>{children}</a>
}

export default function PixelPreviewSource({ preview, file, workbench = false, onOpenFile }) {
  const path = file?.path || 'index.html'
  const expectedDigest = file?.sha256 || preview.entrySha256
  const extension = path.split('.').pop().toLowerCase()
  const language = TEXT_LANGUAGES[extension]
  const [source, setSource] = useState(null)
  const [binarySize, setBinarySize] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [wrapLines, setWrapLines] = useState(false)
  const [viewSource, setViewSource] = useState(false)
  const [showFind, setShowFind] = useState(false)
  const [showExcerpt, setShowExcerpt] = useState(false)
  const [copyError, setCopyError] = useState('')
  const codeRef = useRef(null)
  const optionsRef = useRef(null)
  const anchorPrefix = `portal-doc-${useId().replaceAll(':','')}-`
  const plain = source !== null && needsPlainSource(source)
  const renderedMarkdown = workbench && language === 'markdown' && !viewSource && !plain
  const copyRevision = useRef(0)
  useEffect(() => {
    copyRevision.current++
    let current = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 12000)
    setSource(null); setBinarySize(null); setError(''); setCopied(false); setCopyError('')
    setViewSource(false); setShowFind(false); setShowExcerpt(false)
    if (optionsRef.current) optionsRef.current.open = false
    async function load() {
      try {
        const bytes = await loadArtifactBytes(preview, {path, sha256:expectedDigest, bytes:file?.bytes}, controller.signal)
        if (language) {
          const value = new TextDecoder('utf-8', {fatal:true, ignoreBOM:true}).decode(bytes)
          if (current) setSource(value)
        } else if (current) setBinarySize(bytes.byteLength)
      } catch { if (current) setError('The published source could not be verified. No unverified code is displayed.') }
      finally { clearTimeout(timeout) }
    }
    void load()
    return () => { current = false; copyRevision.current++; controller.abort(); clearTimeout(timeout) }
  }, [preview.siteId, path, expectedDigest, file?.bytes, language, attempt])
  async function copy() {
    if (source === null) return
    const revision = ++copyRevision.current
    setCopied(false)
    try {
      await navigator.clipboard.writeText(source)
      if (revision !== copyRevision.current) return
      setCopied(true); setCopyError('')
    } catch {
      if (revision !== copyRevision.current) return
      setCopied(false); setCopyError('Clipboard access failed. You can select and copy the code manually.')
    }
  }
  function sourceOption(action) {
    action()
    if (optionsRef.current) optionsRef.current.open = false
  }
  const artifact = {path, sha256:expectedDigest, bytes:file?.bytes}
  const breadcrumbParts=[...(typeof preview.relativeDirectory==='string'?preview.relativeDirectory.replaceAll('\\','/').split('/').filter(Boolean):[]),...path.split('/')]
  if (workbench) return <section className="pixel-preview-source pixel-original-source portal-workspace-source" data-wrap-lines={wrapLines} aria-label={`File: ${path}`}>
    <header className="portal-source-header">
      <nav aria-label="File path" className="portal-source-breadcrumb" title={breadcrumbParts.join('/')}>{breadcrumbParts.map((part, index, parts) => <span key={index}>{index > 0 && <ChevronRight size={11} aria-hidden="true"/>}<span aria-current={index === parts.length - 1 ? 'page' : undefined}>{part}</span></span>)}</nav>
      <div className="portal-source-actions">
        {language === 'markdown' && !plain && <button type="button" onClick={() => {setViewSource(value => !value); setShowFind(false); setShowExcerpt(false)}} disabled={source === null}>{viewSource ? 'View rendered' : 'View source'}</button>}
        {language && <button type="button" className="portal-source-icon-button" aria-label={copied ? 'Copied code' : 'Copy code'} title={copied ? 'Copied' : 'Copy source'} onClick={copy} disabled={source === null}>{copied ? <Check size={14}/> : <Copy size={14}/>}</button>}
        <PixelArtifactDownload key={`${preview.siteId}/${path}/${expectedDigest}`} preview={preview} file={artifact}/>
        {language && <details ref={optionsRef} className="portal-source-options" onKeyDown={event => {if (event.key === 'Escape') {event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus()}}}>
          <summary aria-label="Source options" title="Source options"><MoreHorizontal size={16}/></summary>
          <div className="portal-source-options-popover">
            <button type="button" aria-pressed={wrapLines} onClick={() => sourceOption(() => {setWrapLines(value => !value); setViewSource(true)})}>Wrap lines</button>
            <button type="button" disabled={source === null || plain} aria-pressed={showFind} onClick={() => sourceOption(() => {setShowFind(value => !value); setViewSource(true)})}>Find in file</button>
            <button type="button" disabled={source === null} aria-pressed={showExcerpt} onClick={() => sourceOption(() => {setShowExcerpt(value => !value); setViewSource(true)})}>Extract lines</button>
          </div>
        </details>}
      </div>
    </header>
    {error && <div role="alert" className="portal-source-notice"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Retry source</button></div>}
    {copyError && <p role="alert" className="portal-source-notice">{copyError}</p>}
    {source === null && binarySize === null && !error && <p role="status" className="portal-source-notice">Loading file…</p>}
    {binarySize !== null && <div className="portal-source-binary"><PixelLanguageBadge path={path}/><p>No text preview available</p><small>{binarySize.toLocaleString()} bytes · Use Download to open this file.</small></div>}
    {source !== null && <>
      {showFind && !plain && <PixelSourceFind key={`find/${preview.siteId}/${path}/${expectedDigest}`} source={source} codeRef={codeRef}/>}
      {showExcerpt && <div className="portal-source-excerpt"><PixelSourceExcerpt key={`excerpt/${preview.siteId}/${path}/${expectedDigest}`} source={source}/></div>}
      {plain && <p role="status" className="portal-source-notice">Large file · plain text view</p>}
      {renderedMarkdown ? <article className="portal-source-document" aria-label={`Document: ${path}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[documentHeadings,{prefix:anchorPrefix}]]} skipHtml urlTransform={href => markdownLink(href, path) ? href : ''} components={{
          a: ({href, children}) => <MarkdownLink href={href} path={path} anchorPrefix={anchorPrefix} onOpenFile={onOpenFile}>{children}</MarkdownLink>,
          img: ({src, alt}) => <MarkdownLink href={src} path={path} anchorPrefix={anchorPrefix} onOpenFile={onOpenFile}>{alt || 'Image'}</MarkdownLink>,
        }}>{source}</ReactMarkdown>
      </article> : <div className="pixel-code-block portal-source-code"><pre ref={codeRef} tabIndex={0} aria-label={`Code for ${path}`}><PixelCodeLines source={source} language={language}/></pre></div>}
    </>}
  </section>
  return <section className="pixel-preview-source pixel-original-source" data-wrap-lines={wrapLines} aria-label={path === 'index.html' ? 'Published HTML source' : `Source: ${path}`}>
    {source === null && binarySize === null && !error && <p role="status">Verifying source…</p>}
    {binarySize !== null && <p role="status">Binary asset. Its bytes are verified; no text source is available.</p>}
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Retry source</button></div>}
    {copyError && <p role="alert">{copyError}</p>}
    <div className="pixel-code-block">
      <header className="code-block-header"><PixelLanguageBadge path={path}/><span title={path}>{path}</span><PixelArtifactDownload key={`${preview.siteId}/${path}/${expectedDigest}`} preview={preview} file={{path, sha256:expectedDigest, bytes:file?.bytes}}/>{language && <button type="button" aria-label="Copy code" onClick={copy} disabled={source === null}>{copied ? 'Copied' : 'Copy'}</button>}</header>
      {source !== null && <>
        <div className="p-2 text-xs"><button type="button" aria-pressed={wrapLines} onClick={() => setWrapLines(value => !value)}>Wrap lines</button></div>
        <PixelSourceExcerpt key={`excerpt/${preview.siteId}/${path}/${expectedDigest}`} source={source}/>
        {plain ? <p role="status">Large source is shown as plain text. Use browser find or download the file; line search and highlighting are disabled.</p> : <PixelSourceFind key={`find/${preview.siteId}/${path}/${expectedDigest}`} source={source} codeRef={codeRef}/>}
        <pre ref={codeRef} tabIndex={0} aria-label={`Code for ${path}`}><PixelCodeLines source={source} language={language}/></pre>
      </>}

    </div>
    {(source !== null || binarySize !== null) && <p className="pixel-source-verification">Published snapshot · SHA-256 verified{binarySize !== null ? ` · ${binarySize.toLocaleString()} bytes` : ''}</p>}
  </section>
}
