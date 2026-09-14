import { readConversations } from './pixelConversations'

export function exportConversation(chatId) {
  // Read at click time so a sidebar snapshot cannot export an older draft.
  const conversation = readConversations().find(chat => chat.chatId === chatId)
  if (!conversation) throw new Error('Conversation unavailable')
  const archive = {
    schemaVersion: 1,
    kind: 'ods-pixel-conversation',
    exportedAt: new Date().toISOString(),
    conversation,
  }
  const url = URL.createObjectURL(new Blob([JSON.stringify(archive, null, 2)], {type:'application/json'}))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'ods-pixel-' + conversation.chatId + '.json'
  try {
    document.body.append(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
