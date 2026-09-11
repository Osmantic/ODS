import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import MetalMetricIcon from './MetalMetricIcon'
import './dashboard-tokens.css'

const number = value => Number.isFinite(value) && value >= 0 ? value.toLocaleString() : '—'
const dailyTotal = row => {
  if (Number.isFinite(row.total_tokens)) return row.total_tokens
  const values = ['input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'].map(key => row[key]).filter(Number.isFinite)
  return values.length ? values.reduce((sum,value) => sum + value,0) : null
}

const shortNumber = value => Number.isFinite(value) ? Intl.NumberFormat('en', {notation:'compact', maximumFractionDigits:1}).format(value) : '—'
const validValue = value => Number.isFinite(value) && value >= 0
const DAY = 86400000

// Missing days/fields are gaps, not invented zero samples or interpolated activity.
export function tokenLineSegments(rows, key, maximum, interval = DAY) {
  const first = Date.parse(rows[0]?.date)
  const last = Date.parse(rows.at(-1)?.date)
  const segments = []
  let current = []
  rows.forEach((row, index) => {
    if (!validValue(row[key]) || (index && Date.parse(row.date) - Date.parse(rows[index - 1].date) > interval)) {
      if (current.length) segments.push(current)
      current = []
    }
    if (validValue(row[key])) current.push({index, x:last === first ? 180 : 8 + (Date.parse(row.date) - first) / (last - first) * 344, y:128 - row[key] / Math.max(1, maximum) * 100})
  })
  if (current.length) segments.push(current)
  return segments
}

export function visibleMinuteSamples(points, minutes = 15, end = points.at(-1)?.date) {
  if (minutes === 'day') return points
  const timestamp = Date.parse(end)
  if (!Number.isFinite(timestamp)) return []
  const currentMinute = Math.floor(timestamp / 60000) * 60000
  const cutoff = currentMinute - (minutes - 1) * 60000
  return points.filter(row => Date.parse(row.date) >= cutoff && Date.parse(row.date) <= timestamp)
}

function TokenTrend({daily, series, title, label, days, windowLabel}) {
  const [selected, setSelected] = useState(null)
  const [seriesKey,setSeriesKey] = useState('all')
  const visibleSeries = seriesKey === 'all' || !series.some(item=>item.key===seriesKey) ? series : series.filter(item=>item.key===seriesKey)
  const maximum = Math.max(0, ...daily.flatMap(row => visibleSeries.map(item => row[item.key]).filter(validValue)))
  const hasSamples = daily.some(row => visibleSeries.some(item => validValue(row[item.key])))
  const selectedRow = daily.find(row=>row.date===selected)
  const interval = days === 1 ? 60000 : DAY
  const stamp = value => days === 1 && value ? `${new Date(value).toISOString().slice(11,16)} UTC` : value
  return <section className="token-trend" aria-label={title}>
    <header><h3>{title}</h3><span>{days === 1 ? `${windowLabel} · per minute` : `Daily · ${days}d`}</span></header>
    <div className="token-trend-legend" role={series.length > 1 ? 'group' : undefined} aria-label={series.length > 1 ? 'Token series' : undefined}>
      {series.length > 1 && <button aria-label="All token series" aria-pressed={seriesKey==='all'} onClick={()=>setSeriesKey('all')}>Both</button>}
      {series.map(item => series.length > 1 ? <button key={item.key} aria-label={`${item.label} tokens only`} aria-pressed={seriesKey===item.key} onClick={()=>setSeriesKey(item.key)}><i style={{background:item.color}}/>{item.label}</button> : <span key={item.key}><i style={{background:item.color}}/>{item.label}</span>)}
    </div>
    {hasSamples ? <>
      <div className="token-chart-scale" title="Scale maximum for the visible interval and selected series">{shortNumber(maximum)} <span>auto</span></div>
      <svg viewBox="0 24 360 108" preserveAspectRatio="none" role="img" aria-label={label}>
        <line x1="8" y1="28" x2="352" y2="28" className="token-chart-ceiling"/>
        <line x1="8" y1="78" x2="352" y2="78" className="token-chart-grid"/>
        <line x1="8" y1="128" x2="352" y2="128" className="token-chart-grid"/>
        {visibleSeries.map(item => <g key={item.key} data-token-series={item.key}>
          {tokenLineSegments(daily, item.key, maximum, interval).map((segment, index) => <g key={index}>
            <polyline points={segment.map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke={item.color} strokeWidth="1.7" vectorEffect="non-scaling-stroke"/>
            {segment.map(point => <circle key={point.index} cx={point.x} cy={point.y} r={segment.length === 1 || selected === daily[point.index].date ? 2.5 : 0} fill={item.color}/>)}
          </g>)}
        </g>)}
        {tokenLineSegments(daily.map(row => ({...row,sample:0})), 'sample', 1, interval).flat().map(point => <rect key={daily[point.index].date} x={point.x - Math.min(7,172 / Math.max(1,daily.length - 1))} y="24" width={Math.min(14,344 / Math.max(1,daily.length - 1))} height="108" fill="transparent" tabIndex={days === 1 && !daily[point.index].requests ? -1 : 0} role="button" aria-label={`${daily[point.index].date}: ${visibleSeries.map(item => `${item.label} ${number(daily[point.index][item.key])}`).join(', ')}`} onFocus={() => setSelected(daily[point.index].date)} onBlur={() => setSelected(null)} onMouseEnter={() => setSelected(daily[point.index].date)} onMouseLeave={() => setSelected(null)} onClick={() => setSelected(daily[point.index].date)} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); setSelected(daily[point.index].date)}}}><title>{daily[point.index].date}: {series[0].key === 'requests' ? `${number(daily[point.index].requests)} requests` : `${number(daily[point.index].total_tokens)} tokens`}</title></rect>)}
      </svg>
      <div className="token-chart-scale">0</div>
      <div className="token-chart-dates"><span>{stamp(daily[0]?.date)}</span><span>{stamp(daily.at(-1)?.date)}</span></div>
      <p className="token-chart-reading" aria-live="polite">{selectedRow ? `${selectedRow.date} · ${visibleSeries.map(item => `${item.label} ${number(selectedRow[item.key])}`).join(' · ')}` : days === 1 ? 'Visible interval · recorded when requests finish' : 'Hover or focus a day for exact values'}</p>
    </> : <p className="dashboard-empty">{title === 'Token activity' ? 'No daily token samples for this period.' : 'No daily request samples for this period.'}</p>}
  </section>
}
export default function DashboardTokens() {
  const [days, setDays] = useState(1)
  const [minuteWindow,setMinuteWindow] = useState(15)
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState({loading:true})
  useEffect(() => {
    const timer = setInterval(() => {if (document.visibilityState !== 'hidden') setRevision(value => value + 1)}, days === 1 ? 5000 : 30000)
    return () => clearInterval(timer)
  }, [days])
  useEffect(() => {
    const controller = new AbortController()
    const end = new Date()
    const start = new Date(end)
    start.setUTCDate(start.getUTCDate() - days + 1)
    const date = value => value.toISOString().slice(0,10)
    setState(previous => ({...previous, loading:true, error:null}))
    Promise.all([
      fetch(`/api/usage/report?start=${date(start)}&end=${date(end)}`,{signal:controller.signal}).then(response => {if (!response.ok) throw new Error('Token telemetry unavailable.'); return response.json()}),
      fetch('/api/usage/readiness',{signal:controller.signal}).then(response => response.ok ? response.json() : null).catch(() => null),
      days === 1 ? fetch('/api/usage/timeline',{signal:controller.signal}).then(response => response.ok ? response.json() : null).catch(() => null) : null,
    ]).then(([report,readiness,timeline]) => {
      if (report.source?.status && report.source.status !== 'ok') throw new Error('Token telemetry unavailable.')
      if (!controller.signal.aborted) setState({report,readiness,timeline,days})
    }).catch(error => {if (!controller.signal.aborted) setState({error:error.message})})
    return () => controller.abort()
  }, [days,revision])
  const summary = state.report?.summary || {}
  const rows = days === 1 ? (state.timeline?.source?.status === 'ok' && Array.isArray(state.timeline?.points) ? state.timeline.points : []) : state.report?.daily
  const samples = (Array.isArray(rows) ? rows : []).filter(row => row && typeof row.date === 'string' && Number.isFinite(Date.parse(row.date))).map(row => ({...row,total_tokens:dailyTotal(row)})).sort((a,b) => String(a.date).localeCompare(String(b.date)))
  const daily = days === 1 ? visibleMinuteSamples(samples, minuteWindow, state.timeline?.period?.end || samples.at(-1)?.date) : samples
  const tokenSeries = daily.some(row => validValue(row.input_tokens) || validValue(row.output_tokens))
    ? [{key:'input_tokens',label:'Input',color:'#c8c9c9'},{key:'output_tokens',label:'Output',color:'#737779'}]
    : [{key:'total_tokens',label:'Tokens',color:'#c8c9c9'}]
  return <div className="dashboard-tokens">
    <header className="dashboard-token-toolbar"><div><h2>Token usage</h2><p>Recorded inference activity</p></div><div className="dashboard-token-actions"><div className="dashboard-token-period" role="group" aria-label="Token period">{[1,7,30].map(value => <button key={value} aria-label={`${value} ${value === 1 ? 'day' : 'days'}`} aria-pressed={days === value} onClick={() => setDays(value)}>{value}d</button>)}</div><button className="pixel-metal-control" aria-label="Refresh token usage" onClick={() => setRevision(value => value + 1)} disabled={state.loading}><MetalMetricIcon icon={RefreshCw} size={14}/></button></div></header>
    {state.loading && !state.report ? <p role="status" className="dashboard-empty">Loading token usage…</p> : state.error ? <p role="alert" className="dashboard-empty">{state.error} Use refresh to try again.</p> : <>
      {state.readiness?.available !== true && <p className="dashboard-token-notice">{state.readiness?.message || 'Tracking status unavailable. These are recorded totals, not confirmation that tracking is active.'}</p>}
      <div className="dashboard-token-summary">{[['Total tokens','total_tokens',days === 1 ? 'Today (UTC)' : `Last ${days} days`],['Input','input_tokens','Prompt tokens'],['Output','output_tokens','Generated tokens'],['Requests','requests','Recorded calls']].map(([label,key,hint]) => <div className="dashboard-token-total" key={key}><span>{label}</span><strong title={number(summary[key])}>{number(summary[key])}</strong><small>{hint}</small></div>)}</div>
      {days === 1 && <p className="dashboard-token-notice">{state.timeline?.source?.status !== 'ok' ? 'Minute-level history unavailable. Daily totals remain above.' : state.timeline.truncated || state.timeline.invalid_records ? 'Partial minute history: some records are unavailable.' : 'Updates every 5s · tokens and requests per minute'}</p>}
      {days === 1 && <div className="token-window-toolbar"><div role="group" aria-label="Chart time window">{[[15,'Last 15 minutes','15m'],['day','Full day','Full day']].map(([value,label,text])=><button key={value} aria-label={label} aria-pressed={minuteWindow===value} onClick={()=>setMinuteWindow(value)}>{text}</button>)}</div><span>Chart window · daily totals unchanged</span></div>}
      <div className="dashboard-token-layout">
        <TokenTrend key={`tokens-${days}`} daily={daily} series={tokenSeries} days={days} windowLabel={minuteWindow === 15 ? 'Last 15m' : 'Full day'} title="Token activity" label={days === 1 ? 'Recorded tokens today (UTC)' : `Daily recorded tokens for the last ${days} days`}/>
        <TokenTrend key={`requests-${days}`} daily={daily} series={[{key:'requests',label:'Requests',color:'#b8babb'}]} days={days} windowLabel={minuteWindow === 15 ? 'Last 15m' : 'Full day'} title="Requests" label={days === 1 ? 'Recorded requests today (UTC)' : `Daily recorded requests for the last ${days} days`}/>
      </div>
      <section className="token-distribution" aria-label="Token breakdown"><h3>Token distribution</h3><dl className="dashboard-token-breakdown">{[['Input','input_tokens'],['Output','output_tokens'],['Cache read','cache_read_tokens'],['Cache write','cache_write_tokens']].map(([label,key]) => <div key={key}><dt>{label}</dt><span className="token-distribution-track" aria-hidden="true"><i style={{width:`${validValue(summary[key]) && summary.total_tokens > 0 ? Math.min(100,summary[key]/summary.total_tokens*100) : 0}%`}}/></span><dd>{number(summary[key])}</dd></div>)}</dl></section>
      {daily.length > 0 && <details className="dashboard-token-data"><summary>{days === 1 ? 'View minute values' : 'View daily values'}</summary><dl className="dashboard-token-breakdown">{daily.map((row,index) => <div key={row.date || index}><dt>{days === 1 ? `${row.date.slice(11,16)} UTC` : row.date}</dt><dd>{number(row.total_tokens)}</dd></div>)}</dl></details>}
    </>}
  </div>
}
