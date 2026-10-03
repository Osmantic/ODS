import {Check,AlertCircle,Users} from 'lucide-react'

const colors=['#67d9ed','#bb9afa','#f6bd69','#f59cac','#8fddb1','#92aff8']
const states={queued:'Queued',running:'Working',waiting:'Your input',completed:'Finished',failed:'Needs attention',cancelled:'Stopped',skipped:'Skipped',interrupted:'Unconfirmed',stopping:'Stopping'}
export function MiniPortal({index=0,state='queued'}) {
  return <span className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center" aria-hidden="true">
    <svg viewBox="0 0 100 100" className={`h-7 w-7 ${state==='running'?'motion-safe:animate-pulse':''}`}><path d="M25 75C14 75 8 68 8 59C8 49 15 42 25 41C26 28 36 20 48 22C58 23 64 30 66 38C77 34 88 41 89 51C97 55 97 67 90 72C86 75 81 75 76 75Z" fill={colors[index%colors.length]}/></svg>
    {state==='completed' && <Check size={11} className="absolute -bottom-0.5 -right-0.5 rounded-full bg-theme-card text-emerald-400"/>}
    {['failed','interrupted'].includes(state) && <AlertCircle size={11} className="absolute bottom-0 right-0 text-theme-text-secondary"/>}
  </span>
}
export default function PortalAgentDock({controller,onOpen}) {
  const {teams,select}=controller
  const latest=teams[0]
  if(!latest)return null
  const open=onOpen || select
  return <div className="ml-2 flex min-w-0 items-center -space-x-1" aria-label="Portal agent team">
    {latest.mode!=='goal' && latest.agents.map((item,index)=><button key={item.id} type="button" aria-label={`${item.name} · ${states[item.status] || item.status}`} title={`${item.name} · ${states[item.status] || item.status}`} onClick={()=>open({teamId:latest.id,agentId:item.id})} className="rounded-full bg-transparent transition hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-theme-accent"><MiniPortal index={index} state={item.status}/></button>)}
    <button type="button" aria-label="View subagents" title="View subagents" onClick={()=>open(null)} className="inline-flex h-8 w-8 items-center justify-center bg-transparent text-theme-text-muted hover:text-theme-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-theme-accent"><Users size={14}/></button>
  </div>
}
