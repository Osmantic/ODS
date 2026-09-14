import { readConversations } from './pixelConversations'

export function exportConversationMarkdown(chatId) {
  const conversation = readConversations().find(chat => chat.chatId === chatId)
  if (!conversation) throw new Error('Conversation unavailable')
  
  let title = 'Untitled'
  if (conversation.messages && conversation.messages.length > 0) {
     const firstUserMsg = conversation.messages.find(m => m.role === 'user')
     if (firstUserMsg && firstUserMsg.content) {
         title = firstUserMsg.content.slice(0, 50).replace(/\n/g, ' ')
     }
  }

  let markdown = `# Chat: ${title}\n\n`
  for (const msg of conversation.messages || []) {
    if (msg.role === 'system' || msg.role === 'developer') continue
    const roleName = msg.role === 'user' ? '**You:**' : '**Pixel:**'
    markdown += `${roleName}\n\n${msg.content || ''}\n\n---\n\n`
  }
  
  const url = URL.createObjectURL(new Blob([markdown], {type:'text/markdown'}))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'ods-pixel-' + conversation.chatId + '.md'
  try {
    document.body.append(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
