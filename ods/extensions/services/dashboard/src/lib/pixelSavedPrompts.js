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

export const MAX_PROMPT_BACKUP_BYTES = 3 * 1024 * 1024

export function exportSavedPrompts() {
  const prompts = readSavedPrompts().map(({id, title, text}) => ({id, title, text}))
  return JSON.stringify({schemaVersion:1, kind:'ods-saved-prompts', prompts}, null, 2)
}

export function parseSavedPromptBackup(raw) {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > MAX_PROMPT_BACKUP_BYTES) throw new Error('Choose a prompt backup no larger than 3 MB.')
  const value = JSON.parse(raw)
  if (!value || value.schemaVersion !== 1 || value.kind !== 'ods-saved-prompts' || Object.keys(value).length !== 3
    || !Array.isArray(value.prompts) || value.prompts.length > 30 || !value.prompts.every(item => valid(item) && Object.keys(item).length === 3)
    || new Set(value.prompts.map(item => item.id)).size !== value.prompts.length) throw new Error('Choose a valid ODS saved-prompt backup.')
  return value.prompts.map(({id, title, text}) => ({id, title, text}))
}

export function restoreSavedPrompts(raw) {
  const imported = parseSavedPromptBackup(raw)
  // Re-read at confirmation time so an open preview cannot replace later edits.
  const items = readSavedPrompts()
  const ids = new Set(items.map(item => item.id))
  const contents = new Set(items.map(item => JSON.stringify([item.title, item.text])))
  let added = 0
  for (const item of imported) {
    const content = JSON.stringify([item.title, item.text])
    if (contents.has(content)) continue
    let id = item.id, suffix = 1
    while (ids.has(id)) id = `${item.id.slice(0, 60)}-import-${suffix++}`
    items.push({...item, id})
    ids.add(id); contents.add(content); added++
  }
  if (items.length > 30) throw new RangeError('This import would exceed 30 saved prompts. Remove some prompts and try again.')
  if (added) localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(items))
  return {items, added, skipped:imported.length - added}
}
