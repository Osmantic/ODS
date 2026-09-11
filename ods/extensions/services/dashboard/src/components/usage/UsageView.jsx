import {useEffect, useMemo, useState} from 'react'
import {ChevronLeft, ChevronRight, RefreshCw, Search, Download, Activity, Cpu, Layers, Wallet} from 'lucide-react'
import MetalMetricIcon from '../MetalMetricIcon'
import './usage-refined.css'

export const integer = value => Number(value || 0).toLocaleString('en-US')
export const compactNumber = value => new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(Number(value || 0))
const money = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:Number(value)>0 && Number(value)<.01 ? 5 : 2}).format(Number(value || 0))
const dayLabel = value => new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'})
const tokenFields = ['input_tokens','output_tokens','cache_read_tokens','cache_write_tokens']
export const tokens = row => tokenFields.reduce((sum,key)=>sum+Number(row[key] || 0),0)
const sourceNames = {actual_billed:'Billed',priced_from_tokens:'Estimated',local_zero_cost:'Local',untracked:'Unknown cost'}
const sourceDescription = {
  actual_billed:'Explicit provider billing data, when available.',
  priced_from_tokens:'Tracked tokens multiplied by configured prices. An estimate, not an invoice.',
  local_zero_cost:'Local inference has no external API bill. Hardware and electricity costs are not included.',
  untracked:'No reliable pricing or billing source. Cost is unknown, not zero.',
}
const costLabel = row => {
  if (row.cost_source === 'local_zero_cost') return 'Local'
  if (!['actual_billed','priced_from_tokens'].includes(row.cost_source) || row.cost_usd == null || row.cost_usd === '' || !Number.isFinite(Number(row.cost_usd))) return '—'
  return money(row.cost_usd)
}
const metadataValue = value => value || 'unknown'
const requestCountAvailable = (row, source) => row.requests != null && Number.isFinite(Number(row.requests)) && (Number(row.requests)>0 || source?.local_runtime?.request_count_available!==false)
const requestLabel = (row, source) => requestCountAvailable(row,source) ? integer(row.requests) : '—'
const seriesInfo = {input:{field:'input_tokens',label:'Input',color:'#dce1e5'},output:{field:'output_tokens',label:'Output',color:'#909ba8'},cache:{label:'Cache',color:'#66717d'}}

export function csvForRows(rows) {
  const fields=['model','provider','service','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','requests','cost_usd','cost_source']
  const cell=value=>`"${String(value ?? '').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')}"`
  return [fields.join(','),...rows.map(row=>fields.map(key=>cell(row[key])).join(','))].join('\r\n')
}

export default function UsageView({compact=false,report,readiness,loading,error,range,onPrevious,onNext,onRefresh,actionState,onAction}) {
  const [view,setView]=useState('activity')
  const summary=report.summary || {}
  const available=report.source?.status==='ok' && !error && !loading
  const requestsUnavailable=!requestCountAvailable(summary,report.source)
  const stats=[['Total tokens',summary.total_tokens,'Input, output and cache'],['Requests',requestsUnavailable ? null : summary.requests,requestsUnavailable ? 'Counter unavailable' : 'Recorded calls'],['Input',summary.input_tokens,'Prompt tokens'],['Output',summary.output_tokens,'Generated tokens']]
  const tabs=[['activity','Activity',Activity],['models','Models',Cpu],['services','Services',Layers],['costs','Costs',Wallet]]
  return <section className="usage-refined" aria-label="Usage analytics" aria-busy={loading}>
    <header className="usage-intro">
      {!compact && <h1>Usage</h1>}
      <p>Inference, at a glance.</p>
      <div className="usage-period">
        <button aria-label="Previous month" onClick={onPrevious}><ChevronLeft size={15}/></button>
        <span>{new Date(`${range.start}T00:00:00Z`).toLocaleDateString('en-US',{month:'long',year:'numeric',timeZone:'UTC'})}</span>
        <button aria-label="Next month" onClick={onNext}><ChevronRight size={15}/></button>
        <button className="usage-refresh" aria-label="Refresh usage" title="Refresh usage" onClick={onRefresh} disabled={loading}><RefreshCw size={14}/></button>
      </div>
      <div className="usage-source-state"><span className={`usage-status-dot ${available ? 'is-ready' : ''}`}/>{loading ? 'Updating usage…' : available ? 'Recorded activity · refreshes every 10s' : 'Usage data unavailable'}</div>
    </header>
    {readiness.status!=='ready' && !(loading && readiness.status==='unknown') && <section className="usage-notice" aria-label="Tracking status">
      <h2>{readiness.message || 'Usage tracking needs attention.'}</h2><p>{readiness.detail}</p>
      <div>{['enable','restart'].filter(kind=>readiness.actions?.[kind]?.url).map(kind=><button key={kind} disabled={actionState?.status==='running'} onClick={()=>onAction(kind)}>{actionState?.status==='running' ? 'Working…' : readiness.actions[kind].label || (kind==='enable' ? 'Enable Usage Tracking' : 'Restart Token Spy')}</button>)}</div>
    </section>}
    {actionState?.message && <p role="status" className="usage-notice">{actionState.message}</p>}
    {error && <p role="alert" className="usage-notice">Could not load usage. Refresh to try again.</p>}
    {!error && !loading && report.source?.status!=='ok' && <p className="usage-note">{report.source?.detail || 'No reliable telemetry source is available for this period.'}</p>}
    <dl className="usage-metrics">{stats.map(([label,value,note])=><div key={label}><dt>{label}</dt><dd title={available && value!=null ? integer(value) : undefined}>{loading ? '…' : !available || value==null ? '—' : compactNumber(value)}</dd><small>{note}</small></div>)}</dl>
    <nav className="usage-tabs" aria-label="Usage views">{tabs.map(([id,label,Icon])=><button key={id} aria-pressed={view===id} onClick={()=>setView(id)}><MetalMetricIcon icon={Icon} size={14}/>{label}</button>)}</nav>
    <div className="usage-view">
      {view==='activity' && <ActivityView report={report} available={available}/>}
      {view==='models' && <ModelView rows={available ? report.models || [] : []} telemetrySource={report.source}/>}
      {view==='services' && <ServicesView services={available ? report.services || [] : []} total={summary.total_tokens || 0} telemetrySource={report.source}/>}
      {view==='costs' && <CostsView report={report} available={available}/>}
    </div>
  </section>
}

function ActivityView({report,available}) {
  const [series,setSeries]=useState('all')
  const today=new Date().toISOString().slice(0,10)
  const daily=(report.daily || []).filter(day=>day.date<=today)
  const data=daily.map(day=>({date:day.date,input:Number(day.input_tokens || 0),output:Number(day.output_tokens || 0),cache:Number(day.cache_read_tokens || 0)+Number(day.cache_write_tokens || 0),requests:day.requests}))
  const keys=series==='all' ? ['input','output','cache'] : [series]
  return <>
    <header className="usage-section-title"><div><h2>Token activity</h2><p>Daily volume · UTC</p></div></header>
    <div className="usage-series" role="group" aria-label="Token series">{['all','input','output','cache'].map(key=><button key={key} aria-pressed={series===key} onClick={()=>setSeries(key)}>{key==='all' ? 'All' : seriesInfo[key].label}</button>)}</div>
    <div className="usage-charts">
      <Trend label="Tokens per day" data={data} keys={keys} available={available}/>
      <Trend label="Requests per day" data={data} keys={['requests']} available={available && data.some(day=>requestCountAvailable(day,report.source))}/>
    </div>
    <section className="usage-breakdown"><h2>Token distribution</h2><dl>{tokenFields.map((field,index)=><div key={field}><dt>{['Input','Output','Cache read','Cache write'][index]}</dt><dd>{available ? integer(report.summary?.[field]) : '—'}</dd></div>)}</dl></section>
    <details className="usage-details"><summary>View daily values</summary><div className="usage-values-scroll"><table><thead><tr>{['Date (UTC)','Input','Output','Cache','Requests'].map(text=><th key={text}>{text}</th>)}</tr></thead><tbody>{available && data.map(day=><tr key={day.date}><th>{dayLabel(day.date)}</th>{['input','output','cache','requests'].map(key=><td key={key}>{key==='requests' ? requestLabel(day,report.source) : integer(day[key])}</td>)}</tr>)}</tbody></table></div></details>
  </>
}

function Trend({label,data,keys,available,currency=false,gapDays=1}) {
  const [selected,setSelected]=useState(null)
  const max=Math.max(0,...data.flatMap(point=>keys.map(key=>Number(point[key] || 0))))
  const active=data.find(point=>point.date===selected)
  const fmt=currency ? money : compactNumber
  const x=index=>data.length<2 ? 50 : 1+index/(data.length-1)*98
  const y=value=>98-Number(value || 0)/(max || 1)*94
  return <section className="usage-trend" aria-label={label}>
    <header><h3>{label}</h3><span>{available ? fmt(max) : '—'} <small>max</small></span></header>
    <div className="usage-line-legend">{keys.map(key=><span key={key}><i style={{background:seriesInfo[key]?.color || '#c5cbd2'}}/>{seriesInfo[key]?.label || (key==='cost' ? 'USD' : 'Requests')}</span>)}</div>
    {!available ? <div className="usage-chart-empty">No verified data for this period</div> : <>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={label}>
        {[4,51,98].map(value=><line key={value} x1="1" x2="99" y1={value} y2={value} className="usage-gridline" vectorEffect="non-scaling-stroke"/>)}
        {keys.map(key=><g key={key}><path d={data.map((point,index)=>`${index===0 || new Date(point.date)-new Date(data[index-1].date)>86400000*gapDays ? 'M' : 'L'}${x(index)},${y(point[key])}`).join(' ')} fill="none" stroke={seriesInfo[key]?.color || '#c5cbd2'} strokeWidth="1.65" vectorEffect="non-scaling-stroke"/>{data.filter(point=>point[key]>0).map(point=><ellipse key={point.date} cx={x(data.indexOf(point))} cy={y(point[key])} rx=".6" ry="1.6" fill={seriesInfo[key]?.color || '#c5cbd2'}/>)}</g>)}
        {active && <line x1={x(data.indexOf(active))} x2={x(data.indexOf(active))} y1="0" y2="100" stroke="#ffffff40" strokeDasharray="2 3" vectorEffect="non-scaling-stroke"/>}
        {data.map((point,index)=><rect key={point.date} x={Math.max(0,x(index)-50/Math.max(data.length,1))} y="0" width={100/Math.max(data.length,1)} height="100" fill="transparent" tabIndex={0} role="button" aria-label={`${dayLabel(point.date)}: ${keys.map(key=>`${seriesInfo[key]?.label || key} ${currency ? money(point[key]) : integer(point[key])}`).join(', ')}`} onMouseEnter={()=>setSelected(point.date)} onMouseLeave={()=>setSelected(null)} onFocus={()=>setSelected(point.date)} onBlur={()=>setSelected(null)}/>) }
      </svg>
      <div className="usage-chart-axis"><span>{data[0] ? dayLabel(data[0].date) : '—'}</span><span>{data.length ? dayLabel(data.at(-1).date) : '—'}</span></div>
      <p className="usage-chart-reading">{active ? `${dayLabel(active.date)} · ${keys.map(key=>`${seriesInfo[key]?.label || key}: ${currency ? money(active[key]) : integer(active[key])}`).join(' · ')}` : max ? 'Hover or focus a day for exact values.' : 'No recorded activity in this period.'}</p>
    </>}
  </section>
}

function ModelView({rows,telemetrySource}) {
  const [query,setQuery]=useState(''),[provider,setProvider]=useState('all'),[service,setService]=useState('all'),[source,setSource]=useState('all'),[page,setPage]=useState(0)
  const filtered=useMemo(()=>rows.filter(row=>(provider==='all'||metadataValue(row.provider)===provider)&&(service==='all'||metadataValue(row.service)===service)&&(source==='all'||metadataValue(row.cost_source)===source)&&[row.model,row.provider,row.service,row.cost_source].some(value=>String(value || '').toLowerCase().includes(query.trim().toLowerCase()))).sort((a,b)=>tokens(b)-tokens(a)),[rows,query,provider,service,source])
  useEffect(()=>setPage(0),[query,provider,service,source])
  const count=Math.max(1,Math.ceil(filtered.length/8)), current=Math.min(page,count-1)
  function exportCsv(){const url=URL.createObjectURL(new Blob([csvForRows(filtered)],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='ods-usage-by-model.csv';link.click();URL.revokeObjectURL(url)}
  return <>
    <header className="usage-section-title"><div><h2>Usage by Model</h2><p>Ordered by recorded token volume</p></div><button className="usage-text-button" disabled={!filtered.length} onClick={exportCsv}><Download size={14}/>Export CSV</button></header>
    <label className="usage-search"><Search size={14}/><input aria-label="Search models" placeholder="Search models..." value={query} onChange={event=>setQuery(event.target.value)}/></label>
    <details className="usage-filter-details"><summary>Filters{provider!=='all'||service!=='all'||source!=='all' ? ' · active' : ''}</summary><div className="usage-filters">{[['All Providers','provider',provider,setProvider],['All Services','service',service,setService],['All Sources','cost_source',source,setSource]].map(([label,key,value,set])=><select key={key} aria-label={label} value={value} onChange={event=>set(event.target.value)}><option value="all">{label}</option>{[...new Set(rows.map(row=>metadataValue(row[key])))].map(option=><option key={option} value={option}>{sourceNames[option] || option}</option>)}</select>)}</div></details>
    <div className="usage-model-list">{filtered.slice(current*8,current*8+8).map(row=><details key={`${row.model}-${row.provider}-${row.service}-${row.cost_source}`} className="usage-model-row"><summary><span className="usage-model-name"><strong title={row.model}>{row.model || 'Unknown model'}</strong><small>{row.provider || 'unknown'} · {row.service || 'unknown'}</small></span><span className="usage-model-total">{compactNumber(tokens(row))}<small>tokens</small></span><ChevronRight size={13}/></summary><dl>{[['Input',integer(row.input_tokens)],['Output',integer(row.output_tokens)],['Cache read',integer(row.cache_read_tokens)],['Cache write',integer(row.cache_write_tokens)],['Requests',requestLabel(row,telemetrySource)],['Cost',costLabel(row)],['Source',sourceNames[row.cost_source] || 'Unknown cost']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details>)}</div>
    {!filtered.length && <p className="usage-empty">{rows.length ? 'No models match these filters.' : 'No tracked usage for this period'}</p>}
    <footer className="usage-pagination"><span>{filtered.length} {filtered.length===1 ? 'model' : 'models'}</span><div><button aria-label="Previous models page" disabled={current===0} onClick={()=>setPage(current-1)}><ChevronLeft size={14}/></button><span>{current+1} / {count}</span><button aria-label="Next models page" disabled={current===count-1} onClick={()=>setPage(current+1)}><ChevronRight size={14}/></button></div></footer>
  </>
}

function ServicesView({services,total,telemetrySource}) {
  const sorted=[...services].sort((a,b)=>tokens(b)-tokens(a))
  const max=Math.max(1,...sorted.map(tokens))
  return <section><header className="usage-section-title"><div><h2>Tokens by Service</h2><p>All recorded consumers · largest first</p></div></header>{sorted.map(row=><div className="usage-service" key={row.service}><header><strong>{row.service || 'Unknown service'}</strong><span title={integer(tokens(row))}>{compactNumber(tokens(row))}</span></header><div className="usage-service-track"><span style={{width:`${tokens(row)/max*100}%`}}/></div><footer><span>{requestCountAvailable(row,telemetrySource) ? `${integer(row.requests)} requests` : 'Request count unavailable'}</span><span>{total ? (tokens(row)/total*100).toFixed(1) : '0'}% of tokens</span></footer></div>)}{!sorted.length && <p className="usage-empty">No tracked usage for this period</p>}</section>
}

function CostsView({report,available}) {
  const [mode,setMode]=useState('daily')
  const summary=report.summary || {}, today=new Date().toISOString().slice(0,10)
  const daily=(report.daily || []).filter(day=>day.date<=today)
  let running=0
  const values=daily.reduce((rows,day,index)=>{const cost=Number(day.spend_usd || 0);running+=cost;if(mode==='weekly'){const slot=Math.floor(index/7);if(!rows[slot])rows[slot]={date:day.date,cost:0};rows[slot].cost+=cost}else rows.push({date:day.date,cost:mode==='cumulative'?running:cost});return rows},[])
  const localOnly=summary.local_providers>0 && !summary.billing_providers && !summary.untracked_providers
  return <>
    <header className="usage-section-title"><div><h2>Cost Estimate</h2><p>Directional, not a bill</p></div><strong className="usage-cost-value">{available ? money(summary.spend_usd) : '—'}<small>USD</small></strong></header>
    <p className="usage-note">{localOnly ? 'Local inference · no external API charges. Hardware and electricity are not priced by ODS.' : 'Only verified billing or configured provider prices contribute to this total. Unknown costs are excluded.'}</p>
    {!localOnly && <><div className="usage-series" role="group" aria-label="Cost grouping">{['daily','weekly','cumulative'].map(value=><button key={value} aria-pressed={mode===value} onClick={()=>setMode(value)}>{value}</button>)}</div><Trend label="Recorded cost" data={values} keys={['cost']} available={available} currency gapDays={mode==='weekly' ? 7 : 1}/></>}
    <section className="usage-breakdown"><h2>Tracking coverage</h2><dl>{[['Tracked Providers',summary.tracked_providers],['Billing sources',summary.billing_providers],['Local providers',summary.local_providers],['Unknown cost',summary.untracked_providers]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{available ? integer(value) : '—'}</dd></div>)}</dl></section>
    <details className="usage-details"><summary>Tracking Source Guide</summary><dl className="usage-source-guide">{Object.entries(sourceDescription).map(([key,description])=><div key={key}><dt>{sourceNames[key]}</dt><dd>{description}</dd></div>)}</dl></details>
  </>
}
