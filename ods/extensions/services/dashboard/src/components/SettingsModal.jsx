import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search, Settings as Gear, Palette, Activity, Network, HardDrive, RefreshCw, Terminal, Cloud, ShieldCheck, Share2, UserRound, Server, Plug } from 'lucide-react'
import Settings from '../pages/Settings'
import MetalMetricIcon from './MetalMetricIcon'
import ProfileSettings from './settings/ProfileSettings'
import AssistantIdentitySettings from './settings/AssistantIdentitySettings'
import PortalMascotSettings from './settings/PortalMascotSettings'
import { useConnectedAppsEnabled } from './settings/useConnectedApps'
import '../settings-refinement.css'
import '../settings-workspace.css'
const Integrations = lazy(() => import('../pages/ServiceMap'))
const RemoteGPU = lazy(() => import('../pages/RemoteProvider'))
const PixelDiagnostics = lazy(() => import('./settings/PixelDiagnostics'))
const ConnectedApps = lazy(() => import('./settings/ConnectedApps'))

// Sections drawn by their own component rather than the Settings page.
const OWN_COMPONENT = ['profile', 'portal-mascot', 'pixel-diagnostics', 'integrations', 'remote', 'apps']
const sections = [
  ['general', 'General', Gear], ['profile', 'Profile', UserRound], ['appearance', 'Appearance', Palette],
  ['portal-mascot', 'Portal mascot', Cloud],
  ['usage', 'Usage', Activity], ['owner', 'Owner access', UserRound],
  // The id stays "connections" so links to the model provider settings keep working.
  ['connections', 'Portal models', Cloud, 'connections providers model api'],
  // Shown only while ODS has connected apps switched on.
  ['apps', 'Connected apps', Plug, 'google gmail calendar email accounts'],
  ['access', 'Portal access', ShieldCheck],
  ['sharing', 'Model sharing', Share2],
  // Using a model API (an API key) or another computer's GPU lives here.
  ['remote', 'Remote model', Server, 'api key provider openai endpoint url cloud gpu server'],
  ['services', 'Services', Network],
  ['pixel-diagnostics', 'Portal diagnostics', Activity],
  ['storage', 'Storage', HardDrive], ['updates', 'Updates', RefreshCw],
  ['advanced', 'Advanced', Terminal],
  ['integrations', 'Service map', Network],
]

const matches = (query) => ([, label, , keywords = '']) => `${label} ${keywords}`.toLowerCase().includes(query.toLowerCase())

export default function SettingsModal() {
  const content = useRef(null)
  const navigation = useRef(null)
  const [params, setParams] = useSearchParams()
  const {enabled: appsEnabled, unavailable: appsUnavailable, refresh: refreshApps} = useConnectedAppsEnabled()
  const listed = sections.filter(([id]) => id !== 'apps' || appsEnabled !== false)
  const requested = params.get('section')
  // Keep the requested section through a failed read; only a verified opt-out hides it.
  const section = listed.some(([id]) => id === requested) ? requested : 'general'
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
        {listed.filter(matches(query)).map(([id,label,Icon]) => <button key={id} aria-current={section === id ? 'page' : undefined} onClick={() => setSection(id)}><MetalMetricIcon icon={Icon}/><span>{label}</span></button>)}
        {!listed.some(matches(query)) && <p>No settings found.</p>}
      </nav>
    </aside>
    <div ref={content} className="ods-settings-content" aria-label={sections.find(([id]) => id === section)[1]}><div hidden={OWN_COMPONENT.includes(section)}><Settings activeSection={section} /></div>
      {visited.has('profile') && <div hidden={section !== 'profile'} className="space-y-6"><ProfileSettings/><AssistantIdentitySettings/></div>}
      {section === 'portal-mascot' && <PortalMascotSettings/>}
      <Suspense fallback={<p>Loading settings…</p>}>
        {section === 'apps' && (appsEnabled === true ? <ConnectedApps /> : appsUnavailable
          ? <section aria-label="Connected apps" className="space-y-3">
            <h2 className="text-lg font-semibold">Connected apps</h2>
            <p role="status">Connected apps settings are unavailable right now.</p>
            <button type="button" onClick={() => { void refreshApps() }} className="rounded-lg border border-white/20 px-3 py-2">Retry</button>
          </section> : <p>Loading settings…</p>)}
        {section === 'pixel-diagnostics' && <PixelDiagnostics />}
        {visited.has('integrations') && <div hidden={section !== 'integrations'}><Integrations compact /></div>}
        {visited.has('remote') && <div hidden={section !== 'remote'}><RemoteGPU compact /></div>}
      </Suspense>
    </div>
  </section>
}
