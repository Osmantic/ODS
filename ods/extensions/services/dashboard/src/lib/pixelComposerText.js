export function appendComposerText(input, text) {
  if (input === '/') return text
  const separator = input && !input.endsWith(' ') && !input.endsWith('\n') ? ' ' : ''
  return `${input}${separator}${text}`
}

// A message that did not start goes back to the composer. Text typed while
// Portal worked stays too: the unsent message first, then the newer draft.
export function restoreUnsentDraft(current, unsent) {
  if (!unsent || current === unsent || current.startsWith(`${unsent}\n\n`)) return current
  return current.trim() ? `${unsent}\n\n${current}` : unsent
}
