/** Match retained text only: task metadata and model prose about files aren't an index. */
export function conversationExcerpt(chat, query) {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return null
  const fields = [
    ...(Array.isArray(chat.messages) ? chat.messages : []).filter(message => ['user', 'assistant'].includes(message?.role) && typeof message.content === 'string')
      .map(message => ({label:message.role === 'user' ? 'Message' : 'Reply', text:message.content})),
    {label:'Draft', text:typeof chat.draft === 'string' ? chat.draft : ''},
  ]
  for (const {label, text} of fields) {
    const folded = text.toLocaleLowerCase()
    const at = folded.indexOf(needle)
    if (at < 0) continue
    // Case conversion can expand a character (for example İ -> i + dot).
    // Locate boundaries in the original text before choosing surrounding context.
    const originalOffset = (offset, roundUp) => {
      if (folded.length === text.length) return offset
      let low = 0, high = text.length
      while (low < high) {
        const middle = Math.floor((low + high) / 2)
        if (text.slice(0, middle).toLocaleLowerCase().length < offset) low = middle + 1
        else high = middle
      }
      return !roundUp && text.slice(0, low).toLocaleLowerCase().length > offset ? low - 1 : low
    }
    const matchStart = originalOffset(at, false)
    const matchEnd = originalOffset(at + needle.length, true)
    const start = Math.max(0, matchStart - 48)
    const end = Math.min(text.length, Math.max(start + 180, matchEnd))
    return `${label}: ${start ? '…' : ''}${text.slice(start, end).replace(/\s+/g, ' ')}${end < text.length ? '…' : ''}`
  }
  return null
}
