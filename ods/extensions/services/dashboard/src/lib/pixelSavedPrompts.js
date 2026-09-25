export const SAVED_PROMPTS_KEY = 'ods.pixel.saved-prompts.v1'
const valid = item => item && typeof item.id === 'string' && /^[a-z0-9-]{1,80}$/.test(item.id)
  && typeof item.title === 'string' && item.title.trim().length > 0 && item.title.length <= 80
  && typeof item.text === 'string' && item.text.trim().length > 0 && item.text.length <= 16000

export function readSavedPrompts() {
  const items = JSON.parse(localStorage.getItem(SAVED_PROMPTS_KEY) || '[]')
  if (!Array.isArray(items) || items.length > 30 || !items.every(valid) || new Set(items.map(item => item.id)).size !== items.length) throw new Error('Saved prompts could not be read. Existing data was preserved.')
  return items
}

export function writeSavedPrompt(value, previous = null, remove = false) {
  const items = readSavedPrompts()
  const current = items.find(item => item.id === value.id)
  if (previous ? !current || current.title !== previous.title || current.text !== previous.text : current) throw new Error('This prompt changed in another tab. Cancel and reopen it before saving.')
  const next = remove ? items.filter(item => item.id !== value.id) : current ? items.map(item => item.id === value.id ? value : item) : [...items, value]
  if (next.length > 30) throw new Error('You can save up to 30 prompts. Remove one before adding another.')
  if (!remove && !valid(value)) throw new Error('Enter a name (up to 80 characters) and prompt text (up to 16,000 characters).')
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(next))
  return next
}
