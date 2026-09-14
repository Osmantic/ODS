import {useEffect, useRef, useState} from 'react'
import './fitted-library-page.css'

export function fittedPageSize(height, rowHeight) {
  return Math.max(1, Math.floor(Math.max(0, height - 48) / Math.max(1, rowHeight)))
}

export function fittedPageNumbers(current, pages) {
  if (pages <= 7) return Array.from({length: pages}, (_, index) => index + 1)
  const middle = current <= 4 ? [2, 3, 4, 5] : current >= pages - 3
    ? [pages - 4, pages - 3, pages - 2, pages - 1] : [current - 1, current, current + 1]
  const numbers = [1, ...middle, pages]
  return numbers.flatMap((number, index) => index && number - numbers[index - 1] > 1 ? ['gap-' + number, number] : [number])
}

// Pagination uses the utility panel's remaining height, not the whole window.
// Expanded errors/progress can grow naturally; content is never clipped.
export default function FittedLibraryPage({items, label, children, minimumItems = 1}) {
  const root = useRef(null)
  const measured = useRef({width: 0, row: 0})
  const [capacity, setCapacity] = useState(4)
  const [availableHeight, setAvailableHeight] = useState(0)
  const [page, setPage] = useState(1)
  const pages = Math.max(1, Math.ceil(items.length / capacity))
  const current = Math.min(page, pages)
  useEffect(() => {
    const element = root.current
    if (!element) return
    const panel = element.closest('.portal-panel-content')
    const measure = () => {
      const bounds = element.getBoundingClientRect()
      const width = Math.round(bounds.width)
      if (width !== measured.current.width) measured.current = {width, row: 0}
      const rows = [...element.querySelectorAll('.extension-entry, .collection-entry, .model-entry')]
      const row = Math.max(width >= 600 ? 112 : 144, ...rows.map(item => item.getBoundingClientRect().height))
      // Keep the tallest observed row on each width so page changes don't
      // alternate between two capacities when descriptions/statuses differ.
      measured.current.row = Math.max(measured.current.row, row)
      const height = panel
        ? panel.clientHeight - (bounds.top - panel.getBoundingClientRect().top + panel.scrollTop) - 18
        : window.innerHeight - bounds.top - 24
      if (height <= 0) return
      setAvailableHeight(height)
      setCapacity(Math.max(minimumItems, fittedPageSize(height, measured.current.row)))
    }
    measure()
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    observer?.observe(element)
    if (panel) observer?.observe(panel)
    window.addEventListener('resize', measure)
    return () => {observer?.disconnect(); window.removeEventListener('resize', measure)}
  }, [items.length, minimumItems])
  return <section ref={root} className="fitted-library-page" style={{minHeight: availableHeight || undefined}} aria-label={label}>
    {children(items.slice((current - 1) * capacity, current * capacity))}
    {items.length > 0 && <footer className="extensions-page-footer">
      <span>{(current - 1) * capacity + 1}–{Math.min(current * capacity, items.length)} of {items.length}</span>
      {pages > 1 && <nav className="dashboard-pagination" aria-label={`${label} pages`}>
        {fittedPageNumbers(current, pages).map(number => typeof number === 'string'
          ? <span key={number} aria-hidden="true">…</span>
          : <button key={number} aria-label={`Page ${number}`} aria-current={number === current ? 'page' : undefined} onClick={() => setPage(number)}>{number}</button>)}
      </nav>}
    </footer>}
  </section>
}
