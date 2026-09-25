import {parseQuestions} from './questions-schema.mjs';
export {parseQuestions};
export function requestsChoiceQuestion(text) {
  const value=String(text??'').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
  if (/^(?:traduza|translate|repita|repeat|explique|explain|cite|quote)\b/.test(value.trim())) return false;
  return /\b(?:pergunte|perguntas|perguntar|ask|questions)\b/.test(value) && /\b(?:opcoes|escolha|escolher|escolhas|choices|options)\b/.test(value);
}
// A narrow presentation repair when the owner explicitly requested choices.
// Never extract from prose, code, quoted examples, numbered steps or a plan.
export function choiceQuestionFromText(text) {
  if (typeof text!=='string' || text.length>1200) return null;
  const lines=text.trim().split('\n').map(line=>line.trim()).filter(Boolean);
  if (lines.length<3 || lines.length>5 || !/^(?:Qual\b.{0,270}\bprefere|Which\b.{0,260}\bprefer)\?$/i.test(lines[0])) return null;
  if (!lines.slice(1).every(line=>/^[-*] \S/.test(line))) return null;
  return parseQuestions([{id:'preference',question:lines[0],options:lines.slice(1).map(line=>line.slice(2))}]);
}
// Compact models sometimes skip tool discovery and use familiar field aliases.
// Normalize only these unambiguous, side-effect-free question shapes; delivery
// still uses the exact bounded contract and never invents an owner answer.
function normalizeRequest(value) {
  if (!Array.isArray(value) || value.length<1 || value.length>3) return null;
  const normalized=value.map((q,index)=>{
    if (!q || Object.keys(q).some(key=>!['id','question','text','options','choices','wait','required'].includes(key)) ||
        (q.wait!==undefined && q.wait!==true) ||
        (q.required!==undefined && typeof q.required!=='boolean') ||
        (q.options!==undefined && q.choices!==undefined && JSON.stringify(q.options)!==JSON.stringify(q.choices)) ||
        (q.question!==undefined && q.text!==undefined && q.question!==q.text)) return null;
    const options=q.options ?? q.choices;
    return {id:q.id ?? `question_${index+1}`,question:q.question ?? q.text,
      options:Array.isArray(options) ? options.map(option=>typeof option==='string' ? option :
        option && Object.keys(option).join()==='text' ? option.text : null) : null};
  });
  return parseQuestions(normalized);
}
export function questionsText(questions) {
  return questions.map((q,i)=>`${i+1}. ${q.question}\n${q.options.map(option=>`- ${option}`).join('\n')}`).join('\n\n');
}
export function createAskUserTool() {
  return {
    name:'pixel_ods_ask_user',
    description:'Ask the owner 1–3 important clarification questions using an interactive choice card. Use when a missing preference or requirement materially changes the work; do not ask permission for routine steps or use this tool as Operations approval. Write in the owner language. Supply 2–4 concise options per question; custom answers are always available. After calling, stop and wait for the owner answer in the next turn. Never answer for the owner.',
    parameters: {
      type:'object', additionalProperties:false, required:['questions'],
      properties:{questions:{type:'array',minItems:1,maxItems:3,items:{
        type:'object',additionalProperties:false,required:['id','question','options'],
        properties:{id:{type:'string'},question:{type:'string'},options:{type:'array',minItems:2,maxItems:4,items:{type:'string'}}},
      }}},
    },
    async execute(_id,params) {
      const questions=normalizeRequest(params?.questions);
      if (!questions) return {isError:true,content:[{type:'text',text:'Use this exact shape: {"questions":[{"id":"style","question":"Which style?","options":["Minimal","Colorful"]}]}. Provide 1–3 questions with unique short lowercase ids, question text of at most 300 characters and 2–4 distinct string options of at most 160 characters each.'}]};
      return {content:[{type:'text',text:questionsText(questions)+'\n\nWait for the owner to answer. Do not call another tool or choose an answer.'}],details:{status:'awaiting_user',questions}};
    },
  };
}
