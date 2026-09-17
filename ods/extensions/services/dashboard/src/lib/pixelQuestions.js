/* eslint-disable no-control-regex -- Reject control bytes in untrusted public input. */
import {continueGoal} from './portalGoal'

const text = (value,max) => typeof value === 'string' && value.trim() && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value)
export function parseQuestions(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 3) return null
  const ids = new Set()
  for (const q of value) {
    if (!q || Object.keys(q).sort().join() !== 'id,options,question' || !/^[a-z][a-z0-9_]{0,39}$/.test(q.id || '') || ids.has(q.id) || !text(q.question,300) || !Array.isArray(q.options) || q.options.length < 2 || q.options.length > 4 || new Set(q.options).size !== q.options.length || !q.options.every(option=>text(option,160))) return null
    ids.add(q.id)
  }
  return value.map(q=>({id:q.id,question:q.question,options:[...q.options]}))
}
export function parseQuestionsFrame(frame) {
  const value=frame?.pixel_questions
  return frame?.choices?.[0]?.finish_reason === 'stop' && value?.schemaVersion === 1 && Object.keys(value).sort().join() === 'questions,schemaVersion' ? parseQuestions(value.questions) : null
}
export function questionMetadata(message) {
  const questions=message?.role === 'assistant' && parseQuestions(message.questions)
  if (!questions) return {}
  const questionDraft=Object.fromEntries(questions.map(q=>{
    const value=message.questionDraft?.[q.id]
    return [q.id,typeof value === 'string' && value.length<=1000 ? value : '']
  }))
  return {questions,questionDraft}
}
export function answersMessage(questions,answers) {
  if (!parseQuestions(questions) || !questions.every(q=>typeof answers?.[q.id] === 'string' && answers[q.id].trim() && answers[q.id].length<=1000)) return null
  return questions.map(q=>`${q.question}\n${answers[q.id].trim()}`).join('\n\n')
}

/** The answer remains a user turn in the transcript sent to the runtime, but
 * its visible representation belongs to the preceding question card. */
export function isQuestionAnswer(messages,index) {
  const message=messages[index],previous=messages[index-1]
  if(message?.role!=='user' || previous?.role!=='assistant')return false
  const answer=answersMessage(previous.questions,previous.questionDraft)
  return Boolean(answer && (message.content===answer || previous.task?.goal && message.content===continueGoal(messages,index-1,answer)))
}
