import {useId, useState} from 'react'
import {ArrowRight, ArrowLeft, Check, MessageCircle} from 'lucide-react'
import './portal-agent-experience.css'
import {answersMessage} from '../lib/pixelQuestions'

export default function PixelQuestions({questions,answers={},onChange,onSubmit,disabled=false,answered=false}) {
  const [step,setStep]=useState(0)
  const [custom,setCustom]=useState({})
  const id=useId()
  const index=Math.min(step,questions.length-1), question=questions[index]
  const answer=answers[question.id] || ''
  const isCustom=custom[question.id] || (Boolean(answer) && !question.options.includes(answer))
  const complete=answersMessage(questions,answers)
  if (answered) return <section aria-label="Your answers" className="portal-question-card portal-question-answered mt-3 w-full min-w-0 rounded-2xl border border-theme-border p-4 sm:p-5">
    <div className="mb-3 inline-flex items-center gap-2 text-xs text-theme-text-muted"><Check size={14}/>Answered</div>
    <dl className="space-y-3">{questions.map(item=><div key={item.id}><dt className="text-xs text-theme-text-muted">{item.question}</dt><dd className="mt-1 whitespace-pre-wrap text-sm text-theme-text">{answers[item.id]?.trim() || 'Answer unavailable'}</dd></div>)}</dl>
  </section>
  return <section aria-label="Questions for you" className="portal-question-card mt-3 w-full min-w-0 rounded-2xl border border-theme-border p-4 sm:p-5">
    <div className="mb-4 flex items-center justify-between gap-3 text-xs text-theme-text-muted">
      <span className="inline-flex items-center gap-2"><MessageCircle size={15} className="text-theme-accent-light"/>Your input</span>
      <span aria-label="Question progress">{index+1} of {questions.length}</span>
    </div>
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="mb-4 text-base font-medium leading-snug text-theme-text">{question.question}</legend>
      <div className="space-y-2">
        {question.options.map((option,n)=><label key={option} className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition hover:bg-theme-border/30 ${!isCustom && answer===option?'border-theme-accent bg-theme-border/30':'border-transparent'} ${disabled?'cursor-default opacity-60':''}`}>
          <input type="radio" name={`${id}-${question.id}`} value={option} checked={!isCustom && answer===option} onChange={()=>{setCustom(v=>({...v,[question.id]:false}));onChange({...answers,[question.id]:option})}} className="mt-1 accent-theme-accent"/>
          <span className="min-w-0 flex-1 text-sm leading-5 text-theme-text">{option}</span>
          <span aria-hidden="true" className="text-xs text-theme-text-muted">{n+1}</span>
        </label>)}
        <label className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-theme-text-secondary">
          <input type="radio" name={`${id}-${question.id}`} checked={Boolean(isCustom)} onChange={()=>{setCustom(v=>({...v,[question.id]:true}));onChange({...answers,[question.id]:''})}} className="accent-theme-accent"/>Write another answer
        </label>
        {isCustom && <textarea aria-label="Your answer" value={answer} maxLength={1000} rows={3} onChange={e=>onChange({...answers,[question.id]:e.target.value})} placeholder="Tell me what you prefer…" className="w-full resize-y rounded-xl border border-theme-border bg-theme-bg px-3 py-2 text-sm text-theme-text outline-none focus:border-theme-accent"/>}
      </div>
    </fieldset>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-theme-border pt-3">
      <p className="text-xs text-theme-text-muted">Choose what works for you.</p>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {index>0 && <button type="button" aria-label="Previous question" onClick={()=>setStep(index-1)} disabled={disabled} className="rounded-lg p-2 text-theme-text-muted hover:bg-theme-border/30 disabled:opacity-40"><ArrowLeft size={16}/></button>}
        <button type="button" disabled={disabled || !answer.trim() || (index===questions.length-1 && !complete)} onClick={()=>index<questions.length-1?setStep(index+1):onSubmit(complete)} className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg bg-theme-border/30 px-3 py-2 text-sm font-medium text-theme-text transition hover:bg-theme-surface-hover disabled:cursor-not-allowed disabled:opacity-40">
          {index<questions.length-1?'Next':'Continue'}<ArrowRight size={15}/>
        </button>
      </div>
    </div>
  </section>
}
