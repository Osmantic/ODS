import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AtSign, Slash, ShieldCheck, ListChecks, Globe2, CheckCheck } from 'lucide-react'
import PixelMascot from './PixelMascot'
import PixelPromptLibrary from './PixelPromptLibrary'
import { usePortalIdentity } from '../contexts/PortalIdentityContext'

const commands = [
  { title: 'Plan', detail: 'Milestones and completion checks', icon: ListChecks, text: 'Plan this outcome with milestones and exact completion criteria: ' },
  { title: 'Research', detail: 'Current sources and visible provenance', icon: Globe2, text: 'Research this using current sources, inline citations, and evidence-versus-inference labels: ' },
  { title: 'Review', detail: 'Risks and concrete next actions', icon: CheckCheck, text: 'Review this critically, identify real risks, and recommend concrete next actions: ' },
]
const sources = [
  { title: 'Current task', detail: 'Reference the current conversation', icon: AtSign, text: '@current-task ' },
  { title: 'Retained evidence', detail: 'Request evidence; no files are attached automatically', icon: AtSign, text: '@retained-evidence ' },
]
export default function PixelComposerTools({ disabled, input, onInsert, children }) {
  const {displayName} = usePortalIdentity()
  const [menu, setMenu] = useState(null)
  const root = useRef(null)
  const lastTrigger = useRef(null)
  useEffect(() => {
    if (input === '/' && !disabled) { lastTrigger.current = document.activeElement; setMenu('commands') }
  }, [input, disabled])
  useEffect(() => { if (menu) root.current?.querySelector('.pixel-composer-popover button')?.focus() }, [menu])
  useEffect(() => { if (disabled) setMenu(null) }, [disabled])
  useEffect(() => {
    const outside = event => { if (!root.current?.contains(event.target)) setMenu(null) }
    const escape = event => { if (event.key === 'Escape' && menu) { setMenu(null); lastTrigger.current?.focus() } }
    window.addEventListener('pointerdown', outside); window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('pointerdown', outside); window.removeEventListener('keydown', escape) }
  }, [menu])
  function toggle(kind, event) { lastTrigger.current = event.currentTarget; setMenu(value => value === kind ? null : kind) }
  return <div ref={root} className="pixel-composer-tools">
    {menu && <div className="pixel-composer-popover" role="group" onKeyDown={event => {
      const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End']
      if (!keys.includes(event.key)) return
      event.preventDefault()
      const choices = [...event.currentTarget.querySelectorAll('button')]
      const current = choices.indexOf(document.activeElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1
        : (current + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length
      choices[next]?.focus()
    }} aria-label={menu === 'sources' ? 'Mention a source' : 'Prompt commands'}>
      {(menu === 'sources' ? sources : commands).map(item => <button type="button" key={item.title} aria-label={`${item.title} ${item.detail}`} onClick={() => { setMenu(null); onInsert(item.text) }}><item.icon size={17}/><span><strong>{item.title}</strong><small>{item.detail}</small></span></button>)}
    </div>}
    {children}
    <button type="button" disabled={disabled} title="Mention source" aria-label="Mention source" aria-expanded={menu === 'sources'} onClick={event => toggle('sources', event)}><AtSign size={16}/></button>
    <PixelPromptLibrary input={input} disabled={disabled} onInsert={onInsert}/>
    <button type="button" disabled={disabled} title="Prompt commands" aria-label="Open prompt commands" aria-expanded={menu === 'commands'} onClick={event => toggle('commands', event)}><Slash size={16}/></button>
    <span className="pixel-tool-divider" aria-hidden="true"/>
    <Link to="/models" className="pixel-composer-agent" title="Choose the agent model"><PixelMascot name={displayName}/><span>{displayName} agent</span></Link>
    <Link to="/pixel/settings?section=access" title="Assistant access settings"><ShieldCheck size={15}/><span>Permissions</span></Link>
  </div>
}
