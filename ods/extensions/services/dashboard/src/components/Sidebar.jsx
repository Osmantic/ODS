import { NavLink, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Plus, Search, Sparkles, Settings } from 'lucide-react'
import { getSidebarExternalLinks, getSidebarNavItems } from '../plugins/registry'
import { fallbackServiceUrl } from '../lib/serviceUrls'
import PixelHandoffApproval from './PixelHandoffApproval'
import PixelMascot from './PixelMascot'
import ODSLogo from './ODSLogo'
import MetalMetricIcon from './MetalMetricIcon'
import PixelConversationNavigation from './PixelConversationNavigation'
import {useLocalProfile} from '../lib/localProfile'
import UserAvatar from './UserAvatar'

export default function Sidebar({ status, collapsed, onToggle }) {
  const profile = useLocalProfile()
  const { pathname } = useLocation()
  const pixelMode = pathname.startsWith('/pixel')
  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [apiLinks, setApiLinks] = useState([])
  const [serviceTokens, setServiceTokens] = useState({})
  useEffect(() => {
    let active = true
    fetch('/api/external-links').then(r => r.ok ? r.json() : []).then(value => {
      if (active && Array.isArray(value)) setApiLinks(value)
    }).catch(() => {})
    fetch('/api/service-tokens').then(r => r.ok ? r.json() : {}).then(value => {
      if (active && value && typeof value === 'object') setServiceTokens(value)
    }).catch(() => {})
    return () => { active = false }
  }, [])
  const applications = getSidebarExternalLinks({ status, getExternalUrl: fallbackServiceUrl, apiLinks })
    .filter(link => link.healthy || link.alwaysVisible)
  const links = pixelMode ? [
    { path: '/pixel', label: 'Agent', icon: Sparkles },
    { path: '/pixel/settings', label: 'Settings', icon: Settings },
  ] : getSidebarNavItems({ status })
  function closeSearch() { setSearchOpen(false); setQuery('') }
  function toggleSearch() {
    if (searchOpen) { closeSearch(); return }
    if (collapsed) onToggle()
    setSearchOpen(true)
  }
  return <aside className={`pixel-sidebar ${collapsed ? 'is-collapsed' : ''} ${searchOpen ? 'has-search' : ''}`} aria-label={pixelMode ? 'Pixel navigation' : 'ODS navigation'}>
    <div className={`pixel-brand ${pixelMode ? '' : 'ods-brand'}`}>
      <NavLink to={pixelMode ? '/pixel' : '/'} aria-label={pixelMode ? 'Pixel home' : 'ODS home'}>
        <div hidden={pixelMode}><ODSLogo active={!pixelMode} /></div>
        {pixelMode && <><PixelMascot brand /><span>Pixel</span></>}
      </NavLink>
      <button className="pixel-metal-control" onClick={onToggle} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}><MetalMetricIcon icon={collapsed ? ChevronRight : ChevronLeft} size={16}/></button>
    </div>
    <nav className="pixel-nav">
      {pixelMode && <>
        <NavLink to="/" className="pixel-nav-item" title="Back to ODS"><ArrowLeft size={16} /><span>Back to ODS</span></NavLink>
      </>}
      <button className="pixel-nav-item" title="Search navigation" aria-label="Search" onClick={toggleSearch} aria-expanded={searchOpen}><Search size={16} /><span>Search</span></button>
      {searchOpen && !collapsed && <input autoFocus aria-label="Search navigation" placeholder="Find a page…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') { closeSearch(); event.currentTarget.previousElementSibling?.focus() } }} className="pixel-nav-search" />}
      {links.filter(item => item.label.toLowerCase().includes(query.toLowerCase())).map(({ path, label, icon: Icon }) => <NavLink key={path} to={path} end title={label} aria-label={label} onClick={closeSearch} className={({ isActive }) => `pixel-nav-item ${isActive ? 'is-active' : ''}`}><Icon size={16} /><span>{label}</span></NavLink>)}
      {!pixelMode && applications.length > 0 && <details className="pixel-applications" open={query ? true : undefined}>
        <summary aria-label="Applications"><span>{collapsed ? '•••' : 'Applications'}</span><svg className="rail-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m5 6 3 3 3-3"/></svg></summary>
        {applications.filter(link => link.label.toLowerCase().includes(query.toLowerCase())).map(({ key, label, icon: Icon, healthy, url }) => {
          const href = key === 'openclaw' && serviceTokens.openclaw ? `${url}/?token=${encodeURIComponent(serviceTokens.openclaw)}` : url
          return <a key={key} className="pixel-nav-item" title={healthy ? label : `${label} · Offline`} aria-label={label} aria-disabled={!healthy} href={healthy ? href : undefined} target={healthy ? '_blank' : undefined} rel="noopener noreferrer"><Icon size={16} /><span>{label}</span>{!healthy && !collapsed && <small>Offline</small>}</a>
        })}
      </details>}
      <PixelConversationNavigation collapsed={collapsed} />
      {pixelMode && !collapsed && <div className="pixel-sidebar-sections">
        <PixelHandoffApproval label="Approvals" />
      </div>}
    </nav>
    <footer className="pixel-sidebar-footer"><NavLink to="/settings?section=profile" className="sidebar-profile-link" aria-label="Edit your profile" title="Edit your profile"><UserAvatar profile={profile}/><div className="sidebar-profile-copy"><strong>{profile.name || 'Your profile'}</strong><small>{status?.version ? `ODS ${status.version}` : 'Local workspace'}</small></div><MetalMetricIcon icon={Settings} size={14}/></NavLink></footer>
  </aside>
}
