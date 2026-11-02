// The native SDK retains content blocks but omits historical result details.
// Recognize only the separate completion block appended to a paired exec
// result; command output and owner quotations remain ordinary content.
const COMPLETION_NOTE = /^\[ODS Pixel execution\] Exec returned completed with exit code -?\d+\. This result has no background session ID\. Use the returned output; do not invent a session ID or poll a PID\.$/;
// Fixed notices the guard may append after a result's own blocks (the run
// deadline, the failure warning, the edit-early notice): the completion note
// is the last block before them.
const TRAILING_NOTICE = /^\[ODS Pixel (?:time limit|failure warning)\] |^\[ODS Pixel next step\] Many tool calls in this response have read files, and none has changed one with write, edit or apply_patch\./;

export function leakedExecGuidance(reply, messages, ownerText = '', currentReceipts = []) {
  if (typeof reply !== 'string' || !Array.isArray(messages)) return undefined;
  if (ownerText.includes('[ODS Pixel execution]') ||
      /\b(?:quote|explain|describe|show|repeat)\b.{0,100}\b(?:host|internal|execution)\b.{0,40}\b(?:note|guidance)\b/i.test(ownerText)) return undefined;
  const calls = new Set();
  const current = new Map(currentReceipts.map(receipt => [receipt.toolCallId,receipt]));
  const notes = [];
  const outputs = [];
  for (const message of messages) {
    if (!Array.isArray(message?.content)) continue;
    if (message.role === 'assistant') {
      for (const block of message.content) {
        if (block?.type === 'toolCall' && block.name === 'exec' &&
            typeof block.id === 'string' && typeof block.arguments?.command === 'string') calls.add(block.id);
      }
      continue;
    }
    if (message.role !== 'toolResult' || message.toolName !== 'exec' ||
        !calls.delete(message.toolCallId)) continue;
    let end = message.content.length;
    while (end > 1 && message.content[end - 1]?.type === 'text' && TRAILING_NOTICE.test(message.content[end - 1].text)) end--;
    const blocks = message.content.slice(0, end);
    const last = blocks.at(-1);
    const annotated = blocks.length >= 2 && last?.type === 'text' && COMPLETION_NOTE.test(last.text);
    const output = (annotated ? blocks.slice(0, -1) : blocks)
      .filter(block => block?.type === 'text').map(block => block.text).join('\n');
    outputs.push(output);
    if (annotated) notes.push({note:last.text, toolCallId:message.toolCallId});
    // During this run the SDK's final hook sees the original tool message,
    // before tool_result_persist adds guidance. Only our bound persist receipt
    // can establish which separate note the model received in that case.
    const receipt=current.get(message.toolCallId);
    if (!annotated && receipt?.output === output && COMPLETION_NOTE.test(receipt.note))
      notes.push({note:receipt.note,toolCallId:message.toolCallId});
  }
  return notes.find(({note}) => reply.includes(note) &&
    !outputs.some(output => output.includes(note)));
}

export function execGuidanceRevisionInstruction({note}) {
  return 'Remove this separate internal note from your answer: ' + JSON.stringify(note) +
    ' Preserve the actual stdout. Answer the original question using the existing result. ' +
    'Do not call tools or repeat completed work.';
}

export const EXEC_GUIDANCE_LEAK_REASON = 'The answer copied host execution guidance into command output.';
export const EXEC_GUIDANCE_LEAK_DELIVERY =
  'Portal could not separate its internal execution guidance from the answer. ' +
  'The original command output remains in the conversation; no command was repeated to repair this reply.';
