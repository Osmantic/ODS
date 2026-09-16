export default function PixelPreviewHistory({previews, selected, onSelect}) {
  const retained = new Map()
  for (const preview of previews) {
    // Identical content reuses its site ID; order by its latest publication.
    retained.delete(preview.siteId)
    retained.set(preview.siteId, preview)
  }
  const versions = [...retained.values()]
  const currentListed = versions.some(preview => preview.siteId === selected.siteId)
  if (versions.length + (currentListed ? 0 : 1) < 2) return null
  const latest = versions.at(-1)
  return <div className="border-b border-theme-border p-2 text-xs text-theme-text-secondary">
    <label className="flex flex-wrap items-center gap-2">Published version
      <select aria-label="Published version" className="min-w-0 flex-1 rounded border border-theme-border bg-theme-bg p-1" value={selected.siteId} onChange={event => {
        const preview = versions.find(item => item.siteId === event.target.value)
        if (preview) onSelect(preview)
      }}>
        {!currentListed && <option value={selected.siteId}>Current preview · {selected.relativeDirectory}</option>}
        {versions.map((preview,index) => <option key={preview.siteId} value={preview.siteId}>Publication {index+1} · {preview.relativeDirectory}{preview.siteId===latest.siteId?' · Latest retained':''}</option>)}
      </select>
      {selected.siteId !== latest.siteId && <button type="button" onClick={() => onSelect(latest)}>Show latest publication</button>}
    </label>
    {selected.siteId !== latest.siteId && <p className="mt-1">{currentListed ? 'Viewing an earlier saved publication.' : 'Viewing a publication outside retained turn history.'} Workspace files are unchanged.</p>}
  </div>
}
