export function parseConversationImport(value) {
  if (value?.schemaVersion !== 1 || value.kind !== 'ods-pixel-conversation' || value.conversation?.schema !== 1) throw new Error('Choose a Pixel conversation JSON export (version 1).')
  const chat = value.conversation
  if (!Array.isArray(chat.messages) || chat.messages.length > 2000) throw new Error('The export must contain at most 2,000 messages.')
  let bytes = 0
  const messages = chat.messages.map(message => {
    if (!message || !['user','assistant'].includes(message.role) || typeof message.content !== 'string' || message.role === 'user' && message.content.length > 16384) throw new Error('The export contains an invalid message.')
    bytes += new TextEncoder().encode(message.content).byteLength
    if (bytes > 4 * 1024 * 1024) throw new Error('The conversation exceeds the 4 MB retained-text limit.')
    const status = ['done','error','stopped'].includes(message.status) ? message.status : message.status === 'streaming' ? 'stopped' : undefined
    return {role:message.role, content:message.content, ...(message.role === 'assistant' && status ? {status} : {})}
  })
  if (chat.draft !== undefined && (typeof chat.draft !== 'string' || chat.draft.length > 16384)) throw new Error('The export contains an invalid or oversized draft.')
  const draft = chat.draft || ''
  if (!messages.length && !draft.trim()) throw new Error('The export contains no messages or draft.')
  // File-provided IDs, jobs, scopes and publication receipts never become live authority.
  return {schema:1, messages, draft, preview:null, workspaceOpen:false, inFlight:false, interrupted:false, requestId:null, contextStart:0}
}
