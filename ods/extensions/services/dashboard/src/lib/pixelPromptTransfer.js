import {readSavedPrompts, SAVED_PROMPTS_KEY} from './pixelSavedPrompts'

export function parsePromptBackup(text) {
  const backup = JSON.parse(text)
  if (backup?.schemaVersion !== 1 || backup.kind !== 'ods-pixel-prompts' || !Array.isArray(backup.prompts) || backup.prompts.length > 30) throw new Error('Choose a version 1 Pixel prompt backup containing at most 30 prompts.')
  return backup.prompts.map(item => {
    if (!item || typeof item.title !== 'string' || !item.title.trim() || item.title.length > 80 || typeof item.text !== 'string' || !item.text.trim() || item.text.length > 16000) throw new Error('Each prompt needs a name of at most 80 characters and text of at most 16,000 characters.')
    return {title:item.title, text:item.text}
  })
}

export function mergePromptBackup(prompts) {
  // Re-read at confirmation so concurrent edits are retained, not overwritten.
  const next = [...readSavedPrompts()]
  for (const item of prompts) {
    if (!next.some(saved => saved.title === item.title && saved.text === item.text)) next.push({...item,id:`prompt-${crypto.randomUUID()}`})
  }
  if (next.length > 30) throw new Error('Import would exceed 30 saved prompts. Remove some prompts or choose a smaller backup.')
  localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(next))
  return next
}

export function promptBackupText() {
  return JSON.stringify({schemaVersion:1,kind:'ods-pixel-prompts',prompts:readSavedPrompts().map(({title,text}) => ({title,text}))},null,2) + '\n'
}
