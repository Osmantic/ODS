export function appendComposerText(input, text) {
  if (input === '/') return text
  const separator = input && !input.endsWith(' ') && !input.endsWith('\n') ? ' ' : ''
  return `${input}${separator}${text}`
}
