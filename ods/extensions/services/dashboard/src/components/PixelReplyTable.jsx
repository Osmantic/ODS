import {useRef, useState} from 'react'

function csvCell(cell) {
  const text = cell.textContent || ''
  // Spreadsheet programs can interpret formula prefixes even inside quoted CSV.
  const value = /^[\s]*[=+@-]/u.test(text) ? `'${text}` : text
  return `"${value.replaceAll('"', '""')}"`
}

export default function PixelReplyTable({children}) {
  const table = useRef(null)
  const [error, setError] = useState('')
  function download() {
    setError('')
    try {
      const rows = Array.from(table.current.rows, row => Array.from(row.cells, csvCell).join(','))
      const url = URL.createObjectURL(new Blob(['\uFEFF', rows.join('\r\n'), '\r\n'], {type:'text/csv;charset=utf-8'}))
      const link = document.createElement('a')
      link.href = url
      link.download = 'pixel-reply-table.csv'
      document.body.append(link)
      try { link.click() } finally {
        link.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
    } catch {
      setError('This table could not be downloaded. Try again or copy its text.')
    }
  }
  return <div className="my-3 max-w-full">
    <div role="region" aria-label="Scrollable table" tabIndex={0} className="overflow-x-auto rounded border border-theme-border">
      <table ref={table} className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
    <button type="button" onClick={download} className="mt-2 rounded border border-theme-border px-2 py-1 text-xs">Download table CSV</button>
    <p className="mt-1 text-xs text-theme-text-muted">Exports displayed text. Formula-like cells are prefixed with an apostrophe for spreadsheet safety.</p>
    {error && <p role="alert">{error}</p>}
  </div>
}
