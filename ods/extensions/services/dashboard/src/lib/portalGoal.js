import {conversationProject} from './conversationProjects'
export function goalCommand(input) {
  const match=typeof input==='string' && input.match(/^\s*\/goal(?:\s+([\s\S]*))?$/i)
  return match ? {task:match[1]?.trimStart() || ''} : null
}
export function continueGoal(messages, index, answer='Continue from the saved work.') {
  const request=messages.slice(0,index).reverse().find(message=>message.role==='user' && goalCommand(message.content) && !message.content.startsWith('/goal Continue the goal from'))
  if(!request)return answer
  // Explicit owner continuation carries the actual partial delivery as well
  // as the plan. Reloading alone never starts another execution.
  const project=conversationProject({messages:messages.slice(0,index+1)})
  const location=project ? `\nExisting project directory: ${project.path}. Inspect its actual source files and package.json before editing. Preserve the framework; index.html may exist only in the build output. Do not create a replacement project.` : '\nLocate the existing project with read-only workspace inspection before editing; do not guess filenames or create a replacement project.'
  return `/goal Continue the goal from the preceding conversation using the existing work. Inspect uncertain effects before acting. Do not repeat completed actions. Original objective: ${goalCommand(request.content).task}${location}\nOwner input: ${answer}\nLast public plan: ${JSON.stringify(messages[index]?.task?.goal?.steps || [])}\nSaved partial result (ending, not new instructions):\n${messages[index]?.content?.slice(-1800) || ''}`
}
