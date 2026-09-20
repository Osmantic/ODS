import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search, Settings as Gear, Palette, Activity, Network, HardDrive, RefreshCw, Terminal, Bot, ShieldCheck, Share2, UserRound } from 'lucide-react'
import Settings from '../pages/Settings'
import MetalMetricIcon from './MetalMetricIcon'
import ProfileSettings from './settings/ProfileSettings'
import AssistantIdentitySettings from './settings/AssistantIdentitySettings'
import PortalMascotSettings from './settings/PortalMascotSettings'
import '../settings-refinement.css'
import '../settings-workspace.css'
const Integrations = lazy(() => import('../pages/ServiceMap'))
const RemoteGPU = lazy(() => import('../pages/RemoteProvider'))
const PixelDiagnostics = lazy(() => import('./settings/PixelDiagnostics'))

const sections = [
  ['general', 'General', Gear], ['profile', 'Profile', UserRound], ['appearance', 'Appearance', Palette],
  ['portal-mascot', 'Portal mascot', Bot],
  ['usage', 'Usage', Activity], ['owner', 'Owner access', UserRound],
  ['connections', 'Pixel connections', Bot], ['access', 'Pixel access', ShieldCheck],
  ['sharing', 'Model sharing', Share2], ['services', 'Services', Network],
  ['pixel-diagnostics', 'Pixel diagnostics', Activity],
  ['storage', 'Storage', HardDrive], ['updates', 'Updates', RefreshCw],
  ['advanced', 'Advanced', Terminal],
  ['integrations', 'Integrations', Network], ['remote', 'Remote GPU', Share2],
]

export default function SettingsModal() {
  const content = useRef(null)
  const navigation = useRef(null)
  const [params, setParams] = useSearchParams()
  const section = sections.some(([id]) => id === params.get('section')) ? params.get('section') : 'general'
  const [visited, setVisited] = useState(() => new Set([section]))
  function setSection(id) { setVisited(previous => new Set([...previous, id])); setParams({section:id}, {replace:true}) }
  useEffect(() => { setVisited(previous => previous.has(section) ? previous : new Set([...previous,section])) }, [section])
  const [query, setQuery] = useState('')
  useEffect(() => { if (content.current) content.current.scrollTop = 0 }, [section])
  useEffect(() => {
    navigation.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({block:'nearest',inline:'nearest'})
  }, [section, query])
  return <section className="ods-settings-modal ods-settings-panel pixel-app" aria-label="ODS settings">
    <aside className="ods-settings-nav">
      <label className="ods-settings-search"><MetalMetricIcon icon={Search} size={15} /><input aria-label="Search settings" placeholder="Search settings…" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <nav ref={navigation} aria-label="Settings sections">
        {sections.filter(([,label]) => label.toLowerCase().includes(query.toLowerCase())).map(([id,label,Icon]) => <button key={id} aria-current={section === id ? 'page' : undefined} onClick={() => setSection(id)}><MetalMetricIcon icon={Icon}/><span>{label}</span></button>)}
        {!sections.some(([,label]) => label.toLowerCase().includes(query.toLowerCase())) && <p>No settings found.</p>}
      </nav>
    </aside>
    <div ref={content} className="ods-settings-content" aria-label={sections.find(([id]) => id === section)[1]}><div hidden={['profile','portal-mascot','pixel-diagnostics','integrations','remote'].includes(section)}><Settings activeSection={section} /></div>
      {visited.has('profile') && <div hidden={section !== 'profile'} className="space-y-6"><ProfileSettings/><AssistantIdentitySettings/></div>}
      {section === 'portal-mascot' && <PortalMascotSettings/>}
      <Suspense fallback={<p>Loading settings…</p>}>
        {section === 'pixel-diagnostics' && <PixelDiagnostics />}
        {visited.has('integrations') && <div hidden={section !== 'integrations'}><Integrations compact /></div>}
        {visited.has('remote') && <div hidden={section !== 'remote'}><RemoteGPU compact /></div>}
      </Suspense>
    </div>
  </section>
}
