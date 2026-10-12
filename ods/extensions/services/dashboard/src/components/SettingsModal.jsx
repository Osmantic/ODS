import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search, Settings as Gear, Palette, Activity, Network, HardDrive, RefreshCw, Terminal, Cloud, ShieldCheck, Share2, UserRound, Server } from 'lucide-react'
import Settings from '../pages/Settings'
import MetalMetricIcon from './MetalMetricIcon'
import PanelSelect from './PanelSelect'
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
  ['portal-mascot', 'Portal mascot', Cloud],
  ['usage', 'Usage', Activity], ['owner', 'Owner access', UserRound],
  ['connections', 'Portal connections', Cloud], ['access', 'Portal access', ShieldCheck],
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
  const [params, setParams] = useSearchParams()
  const section = sections.some(([id]) => id === params.get('section')) ? params.get('section') : 'general'
  const [visited, setVisited] = useState(() => new Set([section]))
  function setSection(id) { setVisited(previous => new Set([...previous, id])); setParams({section:id}, {replace:true}) }
  useEffect(() => { setVisited(previous => previous.has(section) ? previous : new Set([...previous,section])) }, [section])
  const [query, setQuery] = useState('')
  useEffect(() => { if (content.current) content.current.scrollTop = 0 }, [section])

  const filteredSections = sections.filter(matches(query))
  const currentSection = sections.find(([id]) => id === section) || sections[0]

  const handleSearchKeyDown = (event) => {
    if (event.key === 'Enter' && filteredSections.length > 0) {
      setSection(filteredSections[0][0])
    }
  }

  const selectOptions = (filteredSections.length > 0 ? filteredSections : [currentSection]).map(([id, label, Icon]) => ({
    value: id,
    label,
    icon: <MetalMetricIcon icon={Icon} size={15} />,
  }))

  return (
    <section className="ods-settings-modal ods-settings-panel pixel-app" aria-label="ODS settings">
      <aside className="ods-settings-nav">
        <label className="ods-settings-search">
          <MetalMetricIcon icon={Search} size={15} />
          <input
            aria-label="Search settings"
            placeholder="Search settings…"
            value={query}
            onChange={event => setQuery(event.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
        </label>
        <div className="ods-settings-select-group">
          <PanelSelect
            label="Settings section"
            value={section}
            onChange={setSection}
            options={selectOptions}
          />
          {filteredSections.length === 0 && (
            <p className="ods-settings-empty">No settings found.</p>
          )}
        </div>
      </aside>
      <div ref={content} className="ods-settings-content" aria-label={currentSection[1]}>
        <div hidden={['profile','portal-mascot','pixel-diagnostics','integrations','remote'].includes(section)}>
          <Settings activeSection={section} />
        </div>
        {visited.has('profile') && <div hidden={section !== 'profile'} className="space-y-6"><ProfileSettings/><AssistantIdentitySettings/></div>}
        {section === 'portal-mascot' && <PortalMascotSettings/>}
        <Suspense fallback={<p>Loading settings…</p>}>
          {section === 'pixel-diagnostics' && <PixelDiagnostics />}
          {visited.has('integrations') && <div hidden={section !== 'integrations'}><Integrations compact /></div>}
          {visited.has('remote') && <div hidden={section !== 'remote'}><RemoteGPU compact /></div>}
        </Suspense>
      </div>
    </section>
  )
}
