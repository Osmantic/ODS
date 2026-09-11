import { useEffect, useRef, useState } from 'react'
import { Activity, ChevronRight } from 'lucide-react'
import DashboardTokens from './DashboardTokens'
import MetalMetricIcon from './MetalMetricIcon'

function StatusDot({ tone }) {
  const colors = {green:'bg-emerald-400',red:'bg-red-400',orange:'bg-amber-400',neutral:'bg-theme-text-muted/45'}
  return <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${colors[tone]}`} />
}

function tone(status) {
  if (status === 'healthy') return 'green'
  if (['unhealthy','down','error'].includes(status)) return 'red'
  if (['degraded','starting','restarting'].includes(status)) return 'orange'
  return 'neutral'
}
function Meter({ value, label }) {
  const percent = Math.max(0, Math.min(100, value))
  return <div className="dashboard-metal-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{width:`${percent}%`}}/></div>
}

export default function CompactDashboard({ metrics, services, health }) {
  const [tab, setTab] = useState('overview')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(6)
  const list = useRef(null)
  useEffect(() => {
    if (tab !== 'overview' || !list.current) return
    const panel = list.current.closest('.portal-panel-content')
    if (!panel) return
    const measure = () => {
      const listOffset = list.current.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop
      const available = panel.clientHeight - listOffset - 48
      // Measure the actual collapsed rows; status pills no longer add height.
      const rowHeight = Math.max(36, ...Array.from(list.current.querySelectorAll('summary'), row => row.getBoundingClientRect().height)) + 1
      setPageSize(Math.max(1, Math.min(services.length || 1, Math.floor(available / rowHeight))))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(panel)
    observer.observe(list.current)
    window.addEventListener('resize', measure)
    return () => { observer.disconnect(); window.removeEventListener('resize', measure) }
  }, [tab, services.length])
  const pages = Math.max(1, Math.ceil(services.length / pageSize))
  const currentPage = Math.min(page, pages)
  const deployed = services.filter(service => service.status !== 'not_deployed')
  const scoped = deployed.some(service => typeof service.required === 'boolean') ? deployed.filter(service => service.required) : deployed
  const online = scoped.filter(service => service.status === 'healthy').length
  const percent = scoped.length ? Math.round(online / scoped.length * 100) : null
  return <section className="compact-dashboard" aria-label="Dashboard readings">
    <nav className="dashboard-view-tabs" aria-label="Dashboard views">{['overview','tokens'].map(name => <button key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name === 'overview' ? 'Overview' : 'Tokens'}</button>)}</nav>
    {tab === 'tokens' ? <DashboardTokens /> : <>
    <div className="dashboard-health-strip"><span><StatusDot tone={percent === 100 ? 'green' : percent === 0 ? 'red' : percent === null ? 'neutral' : 'orange'}/>{health.text}</span>{percent !== null && <div><strong>{percent}%</strong><Meter value={percent} label="Services online"/></div>}</div>
    <header><h2>System Overview</h2><p>System metrics and model state</p></header>
    <dl className="dashboard-metric-list">{metrics.map(({icon:Icon,label,value,subvalue,percent:usage,alert}) => <div className={`dashboard-metric-row ${label === 'Model' ? 'is-model' : ''}`} key={label}>
      <dt><MetalMetricIcon icon={Icon}/><span>{label}<small>{subvalue}</small></span></dt>
      <dd><span title={String(value)}>{alert && <StatusDot tone="orange"/>}{value}</span>{Number.isFinite(usage) && <Meter value={usage} label={`${label} utilization`}/>}</dd>
    </div>)}</dl>
    <header className="dashboard-services-heading"><div><h2>Services</h2><p>Current service health</p></div><span>{services.length} services</span></header>
    <div ref={list} className="dashboard-service-list">{services.slice((currentPage - 1) * pageSize,currentPage * pageSize).map((service,index) => <details key={service.id || service.name || index}>
      <summary><Activity size={13}/><span className="dashboard-service-name">{service.name || service.id}</span><span className="dashboard-status-badge"><StatusDot tone={tone(service.status)}/>{(service.status || 'unknown').replaceAll('_',' ')}</span><ChevronRight className="dashboard-service-chevron" size={12}/></summary>
      <dl><div><dt>Status</dt><dd>{(service.status || 'unknown').replaceAll('_',' ')}</dd></div>{service.port && <div><dt>Port</dt><dd>{service.port}</dd></div>}{service.id && <div><dt>Service</dt><dd>{service.id}</dd></div>}</dl>
    </details>)}</div>
    {!services.length && <p className="dashboard-empty">No service telemetry available.</p>}
    {pages > 1 && <nav className="dashboard-pagination" aria-label="Service pages">{Array.from({length:pages},(_,index) => index + 1).map(number => <button key={number} aria-label={`Page ${number}`} aria-current={currentPage === number ? 'page' : undefined} onClick={() => setPage(number)}>{number}</button>)}</nav>}
    </>}
  </section>
}
