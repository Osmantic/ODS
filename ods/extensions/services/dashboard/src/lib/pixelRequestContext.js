const MAX_INPUT_LEN = 16 * 1024
const MAX_REQUEST_MESSAGES = 50
const MAX_TOTAL_MESSAGE_BYTES = 256 * 1024

export function boundedHistory(messages, nextUserContent) {
  const encoder = new TextEncoder()
  const budget = MAX_TOTAL_MESSAGE_BYTES - encoder.encode(nextUserContent).byteLength
  const selected = []
  let bytes = 0
  for (let index = messages.length - 1; index >= 0 && selected.length < MAX_REQUEST_MESSAGES - 2; index -= 1) {
    const { role } = messages[index]
    // The transport's per-message cap is not a transcript storage limit.
    // Bound only the copy sent to the model; keep the complete reply in chat.
    const omission = '\n[Earlier response shortened for model context.]'
    const original = messages[index].content
    const content = original.length > MAX_INPUT_LEN
      ? original.slice(0, MAX_INPUT_LEN - omission.length) + omission : original
    const size = encoder.encode(content).byteLength
    if (bytes + size > budget) break
    selected.unshift({ role, content })
    bytes += size
  }
  while (selected[0]?.role === 'assistant') selected.shift()
  return selected
}
