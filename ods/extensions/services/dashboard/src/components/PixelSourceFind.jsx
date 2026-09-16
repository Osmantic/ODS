import {useEffect, useMemo, useState} from 'react'
import './pixel-source-find.css'

export default function PixelSourceFind({source, codeRef}) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const [matchCase, setMatchCase] = useState(false)
  const [requestedLine, setRequestedLine] = useState('')
  const [jumpedLine, setJumpedLine] = useState(null)
  const lineCount = source.replace(/\n$/u, '').split('\n').length
  const target = Number(requestedLine)
  const canJump = /^\d+$/.test(requestedLine) && Number.isSafeInteger(target) && target >= 1 && target <= lineCount
  const matches = useMemo(() => {
    if (!query) return []
    const needle = matchCase ? query : query.toLocaleLowerCase()
    return source.split('\n').flatMap((line, at) => (matchCase ? line : line.toLocaleLowerCase()).includes(needle) ? [at + 1] : [])
  }, [source, query, matchCase])
  const line = jumpedLine ?? matches[index] ?? matches[0]
  useEffect(() => {
    if (!line) return
    const row = codeRef.current?.querySelector(`[data-line="${line}"]`)
    row?.setAttribute('data-source-find-current', '')
    row?.scrollIntoView?.({block:'nearest', inline:'nearest'})
    return () => row?.removeAttribute('data-source-find-current')
  }, [line, codeRef, source])
  function move(direction) {setIndex(value => matches.length ? (value + direction + matches.length) % matches.length : 0)}
  return <div className="pixel-source-find" role="search" aria-label="Search verified source">
    <input type="search" aria-label="Find in source" placeholder="Find in source…" value={query} onChange={event => {setQuery(event.target.value); setIndex(0); setJumpedLine(null)}} onKeyDown={event => {
      if (event.nativeEvent?.isComposing) return
      if (event.key === 'Enter') {event.preventDefault(); move(event.shiftKey ? -1 : 1)}
      if (event.key === 'Escape') {setQuery(''); setIndex(0); setJumpedLine(null)}
    }}/>
    <label><input type="checkbox" checked={matchCase} onChange={event => {setMatchCase(event.target.checked); setIndex(0); setJumpedLine(null)}}/> Match case</label>
    <button type="button" disabled={!matches.length} aria-label="Previous matching line" onClick={() => move(-1)}>Previous</button>
    <button type="button" disabled={!matches.length} aria-label="Next matching line" onClick={() => move(1)}>Next</button>
    <form onSubmit={event => {event.preventDefault(); if (canJump) {setJumpedLine(target); setQuery(''); setIndex(0)}}}>
      <input type="number" aria-label="Go to source line" min="1" max={lineCount} step="1" value={requestedLine} onChange={event => setRequestedLine(event.target.value)}/>
      <button type="submit" disabled={!canJump}>Go to line</button>
    </form>
    {jumpedLine && <span role="status">Line {jumpedLine} of {lineCount}</span>}
    {query && <span role="status">{matches.length ? `${Math.min(index, matches.length - 1) + 1} of ${matches.length} matching lines · Line ${line}` : 'No matching lines'}</span>}
  </div>
}
