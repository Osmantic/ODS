import {useEffect, useMemo, useState} from 'react'
import './pixel-source-find.css'

export default function PixelSourceFind({source, codeRef}) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const matches = useMemo(() => {
    if (!query) return []
    const needle = query.toLocaleLowerCase()
    return source.split('\n').flatMap((line, at) => line.toLocaleLowerCase().includes(needle) ? [at + 1] : [])
  }, [source, query])
  const line = matches[index] ?? matches[0]
  useEffect(() => {
    if (!line) return
    const row = codeRef.current?.querySelector(`[data-line="${line}"]`)
    row?.setAttribute('data-source-find-current', '')
    row?.scrollIntoView?.({block:'nearest', inline:'nearest'})
    return () => row?.removeAttribute('data-source-find-current')
  }, [line, codeRef, source])
  function move(direction) {setIndex(value => matches.length ? (value + direction + matches.length) % matches.length : 0)}
  return <div className="pixel-source-find" role="search" aria-label="Search verified source">
    <input type="search" aria-label="Find in source" placeholder="Find in source…" value={query} onChange={event => {setQuery(event.target.value); setIndex(0)}} onKeyDown={event => {
      if (event.nativeEvent?.isComposing) return
      if (event.key === 'Enter') {event.preventDefault(); move(event.shiftKey ? -1 : 1)}
      if (event.key === 'Escape') {setQuery(''); setIndex(0)}
    }}/>
    <button type="button" disabled={!matches.length} aria-label="Previous matching line" onClick={() => move(-1)}>Previous</button>
    <button type="button" disabled={!matches.length} aria-label="Next matching line" onClick={() => move(1)}>Next</button>
    {query && <span role="status">{matches.length ? `${Math.min(index, matches.length - 1) + 1} of ${matches.length} matching lines · Line ${line}` : 'No matching lines'}</span>}
  </div>
}
