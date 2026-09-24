import {useEffect, useState} from 'react'
import {Download} from 'lucide-react'

const counters = ['input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','total_tokens','requests']
const text = value => typeof value === 'string' ? value : null
const numeric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
const projectNumbers = (row, fields) => Object.fromEntries(fields.map(key => [key,numeric(row?.[key])]))
const projectRows = (rows, names, numbers) => (Array.isArray(rows) ? rows : []).map(row => ({...Object.fromEntries(names.map(key => [key,text(row?.[key])])),...projectNumbers(row,numbers)}))
const costRows = (rows, sourceKey) => rows.map(row => ({...row,cost_usd:row[sourceKey] === 'local_zero_cost' ? 0 : ['actual_billed','priced_from_tokens'].includes(row[sourceKey]) ? row.cost_usd : null}))

function snapshot(report, range) {
  const runtime = report.source?.local_runtime
  return {
    schemaVersion:1, kind:'ods-usage-report', exportedAt:new Date().toISOString(), timezone:'UTC',
    period:{start:range.start,end:range.end}, scope:'entire-selected-period',
    source:{name:text(report.source?.name),status:text(report.source?.status),localRuntime:runtime ? {
      status:text(runtime.status), includedInTotals:runtime.included_in_totals === true,
      requestCountAvailable:typeof runtime.request_count_available === 'boolean' ? runtime.request_count_available : null,
      counters:projectRows(runtime.counters,['runtime','service','model','request_count_source'],['input_tokens','output_tokens','requests']),
    } : null},
    notes:['Screen filters are not applied.', 'Costs retain their reported source; estimates are not invoices. Missing or invalid numeric values are null. Cumulative runtime counters are separate from period totals.'],
    summary:projectNumbers(report.summary,[...counters,'spend_usd','paid_cost_usd','local_cost_usd','tracked_providers','billing_providers','local_providers','untracked_providers']),
    daily:projectRows(report.daily,['date'],[...counters,'spend_usd']),
    models:costRows(projectRows(report.models,['model','provider','service','cost_source'],[...counters,'cost_usd']),'cost_source'),
    services:projectRows(report.services,['service'],[...counters,'cost_usd']),
    sources:costRows(projectRows(report.sources,['source'],[...counters,'cost_usd']),'source'),
  }
}

export default function UsageReportDownload({report,range,available}) {
  const [error,setError] = useState('')
  const matches = report.period?.start === range.start && report.period?.end === range.end
  useEffect(() => setError(''), [range.start,range.end])
  function download() {
    if (!available || !matches) return
    setError('')
    let url, anchor
    try {
      const blob = new Blob([JSON.stringify(snapshot(report,range),null,2)], {type:'application/json'})
      if (blob.size > 8 * 1024 * 1024) throw new Error('This report is too large to export (8 MiB limit).')
      url = URL.createObjectURL(blob)
      anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `ods-usage-${range.start}-to-${range.end}.json`
      document.body.append(anchor)
      anchor.click()
    } catch {setError('Report download could not be started. Try again with a smaller report if it exceeds 8 MiB.')}
    finally {anchor?.remove(); if (url) setTimeout(() => URL.revokeObjectURL(url),1000)}
  }
  return <div><button type="button" className="usage-text-button" disabled={!available || !matches} title="Download the entire selected month, including cost-source and runtime-counter metadata. Screen filters are not applied." onClick={download}><Download size={14}/>Download report JSON</button>{error && <p role="alert" className="usage-note">{error}</p>}</div>
}
