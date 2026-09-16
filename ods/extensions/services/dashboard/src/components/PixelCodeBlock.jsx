import { Children, isValidElement } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import './pixel-file-changes.css'

const LANGUAGES = {html:'html',htm:'html',css:'css',js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'jsx',ts:'typescript',tsx:'tsx',py:'python',json:'json',svg:'xml',xml:'xml',md:'markdown',markdown:'markdown',sh:'bash',yml:'yaml',yaml:'yaml',txt:'text',map:'json',csv:'text',tsv:'text'}
export const fileLanguage = path => LANGUAGES[String(path).split('.').pop().toLowerCase()] || 'text'
export function PixelLanguageBadge({path}) {
  const language = fileLanguage(path)
  const extension = String(path).split('.').pop().toLowerCase()
  const badge = {html:'HTML',javascript:'JS',typescript:'TS',python:'PY',markdown:'MD',xml:extension === 'svg' ? 'SVG' : 'XML'}[language] || (language === 'text' ? extension.toUpperCase().slice(0,4) || 'TXT' : language.toUpperCase().slice(0,4))
  return <b className="code-language-badge" data-language={language === 'html' || language === 'xml' ? 'markup' : language} aria-hidden="true">{badge}</b>
}

// Highlighting produces React spans, not raw HTML. Split their text into source
// lines while keeping token classes, so generated markup is never executable.
function highlightedLines(children) {
  const lines = [[]]
  function visit(node, classes = []) {
    Children.forEach(node, child => {
      if (typeof child === 'string' || typeof child === 'number') {
        String(child).split('\n').forEach((text, index) => {
          if (index) lines.push([])
          if (text) lines.at(-1).push({text, className:classes.join(' ')})
        })
      } else if (isValidElement(child)) visit(child.props.children, [...classes, child.props.className || ''])
    })
  }
  visit(children)
  return lines
}

export const needsPlainSource = source => source.length > 128 * 1024 || source.split('\n', 2001).length > 2000

export function PixelCodeLines({source, language = 'text', renderLine}) {
  // Diff rows have their own bounded contract and must retain annotations.
  if (!renderLine && needsPlainSource(String(source))) return <code>{String(source)}</code>
  const text = String(source).replace(/\n$/u, '')
  const rows = text.split('\n')
  const render = tokens => rows.map((row, index) => {
    // Markdown highlighting can normalize CRLF and other source text.
    // Use tokens only when they reproduce this verified source line exactly.
    const lineTokens = tokens?.[index]
    const content = lineTokens?.map(token => token.text).join('') === row
      ? lineTokens.map((token, part) => <span className={token.className || undefined} key={part}>{token.text}</span>)
      : row
    return renderLine ? renderLine(content, index) : <span className="code-line" data-line={index + 1} key={index}><span className="code-line-content">{content}{index < rows.length - 1 || String(source).endsWith('\n') ? '\n' : ''}</span></span>
  })
  if (text.length > 128 * 1024 || language === 'text') return <code>{render()}</code>
  const fence = '`'.repeat([...text.matchAll(/`+/g)].reduce((max,match) => Math.max(max,match[0].length),2) + 1)
  return <ReactMarkdown rehypePlugins={[[rehypeHighlight, {detect:false, ignoreMissing:true}]]} components={{
    pre: ({children}) => children,
    code: ({children}) => <code>{render(highlightedLines(children))}</code>,
  }}>{`${fence}${language}\n${text}\n${fence}`}</ReactMarkdown>
}
