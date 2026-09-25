const PREFIX = 'ods.pixel.chat-labels.v1.'
const defaults = {title:'', pinned:false, archived:false}

function load(chatId) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(chatId)) throw new Error('Invalid conversation identity')
  const value = JSON.parse(localStorage.getItem(PREFIX + chatId) || 'null')
  if (value === null) return {...defaults}
  if (typeof value.title !== 'string' || value.title.length > 80 || typeof value.pinned !== 'boolean' || typeof value.archived !== 'boolean') {
    throw new Error('Saved labels could not be read. Existing data was preserved.')
  }
  return {title:value.title, pinned:value.pinned, archived:value.archived}
}

export function conversationLabels(chatId) {
  try { return load(chatId) } catch { return {...defaults} }
}

export function saveConversationLabels(chatId, labels, expected) {
  const current = load(chatId) // Refuse to overwrite unreadable metadata.
  if (expected && Object.keys(defaults).some(key => current[key] !== expected[key])) throw new Error('Labels changed in another tab. Cancel and reopen to review the latest labels.')
  if (typeof labels.title !== 'string' || labels.title.trim().length > 80 || typeof labels.pinned !== 'boolean' || typeof labels.archived !== 'boolean') throw new Error('Invalid conversation labels')
  localStorage.setItem(PREFIX + chatId, JSON.stringify({...labels, title:labels.title.trim()}))
  window.dispatchEvent(new Event('ods:pixel-conversations-changed'))
}

export function deleteConversationLabels(chatId) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(chatId)) throw new Error('Invalid conversation identity')
  localStorage.removeItem(PREFIX + chatId)
}
