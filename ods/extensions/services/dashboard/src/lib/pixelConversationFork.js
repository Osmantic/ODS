import { readConversations, saveConversation, SELECT_EVENT } from './pixelConversations'

export function forkConversation(chatId) {
  const conversation = readConversations().find(chat => chat.chatId === chatId)
  if (!conversation) throw new Error('Conversation unavailable')

  const newChatId = 'ods-pixel-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6)
  const forkedChat = {
    ...conversation,
    chatId: newChatId,
    messages: JSON.parse(JSON.stringify(conversation.messages))
  }
  
  saveConversation(forkedChat)
  window.dispatchEvent(new CustomEvent(SELECT_EVENT, { detail: newChatId }))
}
