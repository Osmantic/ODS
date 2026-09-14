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
    const at = text.toLocaleLowerCase().indexOf(needle)
    if (at < 0) continue
    const start = Math.max(0, at - 48)
    const end = Math.min(text.length, start + Math.max(180, needle.length))
    return `${label}: ${start ? '…' : ''}${text.slice(start, end).replace(/\s+/g, ' ')}${end < text.length ? '…' : ''}`
  }
  return null
}
