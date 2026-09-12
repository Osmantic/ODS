import {useMemo, useRef, useState} from 'react'
import {createPortal} from 'react-dom'

const PAGE_SIZE = 25

export default function PixelConversationFind({messages, onNavigate}) {
  const dialog = useRef(null), trigger = useRef(null), input = useRef(null)
  const [query,setQuery] = useState('')
  const [page,setPage] = useState(0)
  const matches = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (!needle) return []
    return messages.flatMap((message,index) => {
      if (!['user','assistant'].includes(message.role) || typeof message.content !== 'string') return []
      const at = message.content.toLocaleLowerCase().indexOf(needle)
      if (at < 0) return []
      const start = Math.max(0,at - 48), end = Math.min(message.content.length,at + needle.length + 120)
      return [{index,role:message.role === 'user' ? 'prompt' : 'reply',excerpt:`${start ? '…' : ''}${message.content.slice(start,end)}${end < message.content.length ? '…' : ''}`}]
    })
  },[messages,query])
  const lastPage = Math.max(0,Math.ceil(matches.length / PAGE_SIZE) - 1)
  const currentPage = Math.min(page,lastPage)
  const shown = matches.slice(currentPage * PAGE_SIZE,(currentPage + 1) * PAGE_SIZE)
  function close(restore = true) {
    dialog.current.close()
    const options = trigger.current.closest('details')
    options?.removeAttribute('open')
    if (restore) (options?.querySelector('summary') || trigger.current).focus()
  }
  function choose(match) {if (match) {close(false); onNavigate(match.index)}}
  return <>
    <button ref={trigger} type="button" className="block p-2 text-xs" disabled={!messages.length} onClick={() => {dialog.current.showModal(); input.current.focus()}}>Find in this conversation</button>
    {createPortal(<dialog ref={dialog} className="chat-delete-dialog" style={{maxHeight:'calc(100dvh - 32px)',overflowY:'auto'}} aria-label="Find in this conversation" onCancel={event => {event.preventDefault();close()}}>
      <h3>Find in this conversation</h3>
      <input ref={input} type="search" aria-label="Search message text" placeholder="Find a word or phrase" maxLength={1000} value={query} className="my-2 w-full rounded border border-theme-border bg-theme-bg p-2 text-theme-text" onChange={event => {setQuery(event.target.value);setPage(0)}} onKeyDown={event => {
        if (event.nativeEvent.isComposing) return
        if (event.key === 'Enter') {event.preventDefault();choose(shown[0])}
      }}/>
      <p role="status">{!query.trim() ? 'Search prompts and replies in this conversation.' : !matches.length ? 'No matching messages.' : `${matches.length} matching message${matches.length === 1 ? '' : 's'} · Showing ${currentPage * PAGE_SIZE + 1}–${currentPage * PAGE_SIZE + shown.length}`}</p>
      <ul>{shown.map(match => <li key={match.index} className="my-2">
        <button type="button" aria-label={`Go to ${match.role} message ${match.index + 1}`} className="w-full rounded border border-theme-border p-2 text-left text-xs" onClick={() => choose(match)}>
          <strong>{match.role === 'prompt' ? 'Prompt' : 'Reply'} · Message {match.index + 1}</strong>
          <span className="mt-1 block whitespace-pre-wrap break-words">{match.excerpt}</span>
        </button>
      </li>)}</ul>
      {lastPage > 0 && <div className="flex gap-3 text-xs">
        <button type="button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous results</button>
        <button type="button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next results</button>
      </div>}
      <footer><button type="button" onClick={() => close()}>Close message search</button></footer>
    </dialog>,document.body)}
  </>
}
