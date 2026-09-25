import {useState} from 'react'
import {Download} from 'lucide-react'

const fields = ['date', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'requests']
const numberCell = value => value != null && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 ? String(Number(value)) : ''

export default function DailyUsageExport({daily, available, source}) {
  const [error, setError] = useState(null)
  // Only the canonical UTC date and numeric counters enter this export.
  const rows = daily.filter(day => /^\d{4}-\d{2}-\d{2}$/.test(day.date))
  function download() {
    setError(null)
    let url
    let link
    try {
      const csv = [fields.join(','), ...rows.map(day => fields.map(field => {
        if (field === 'date') return day.date
        if (field === 'requests' && source?.local_runtime?.request_count_available === false && !(Number(day.requests) > 0)) return ''
        return numberCell(day[field])
      }).join(','))].join('\r\n')
      url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}))
      link = document.createElement('a')
      link.href = url
      link.download = `ods-daily-usage-${rows[0].date}-to-${rows.at(-1).date}.csv`
      document.body.appendChild(link)
      link.click()
    } catch (cause) {
      setError(`Daily usage export failed: ${cause.message}`)
    } finally {
      link?.remove()
      // The browser consumes the download navigation asynchronously. Keep the
      // Blob URL alive for one task window before releasing it.
      if (url) setTimeout(() => URL.revokeObjectURL(url), 1000)
    }
  }
  return <div>
    <button type="button" className="usage-text-button" disabled={!available || !rows.length} onClick={download}><Download size={14}/>Export daily CSV</button>
    {error && <p role="alert" className="usage-note">{error}</p>}
  </div>
}
