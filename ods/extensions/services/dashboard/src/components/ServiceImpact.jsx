import { useState } from 'react'

function affectedPaths(id, edges) {
  const paths = new Map([[id, [id]]])
  const pending = [id]
  for (let index = 0; index < pending.length; index += 1) {
    const dependency = pending[index]
    for (const edge of edges) {
      if (edge.target !== dependency || paths.has(edge.source)) continue
      paths.set(edge.source, [edge.source, ...paths.get(dependency)])
      pending.push(edge.source)
    }
  }
  paths.delete(id)
  return paths
}

export default function ServiceImpact({ nodes, edges }) {
  const [selected, setSelected] = useState('')
  const selectedNode = nodes.find(node => node.id === selected)
  const paths = selectedNode ? affectedPaths(selected, edges) : new Map()
  const names = new Map(nodes.map(node => [node.id, node.name]))
  return (
    <section className="mb-4 rounded-xl border border-theme-border bg-theme-card p-4 text-sm text-theme-text" aria-label="Service impact explorer">
      <label className="font-semibold">Explore service impact
        <select aria-label="Service to inspect" value={selectedNode ? selected : ''} onChange={event => setSelected(event.target.value)} className="ml-3 rounded border border-theme-border bg-theme-card p-2">
          <option value="">Choose a service</option>
          {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(node => <option key={node.id} value={node.id}>{node.name}</option>)}
        </select>
      </label>
      {selectedNode && <div className="mt-3">
        <p>{paths.size} potentially affected services if {selectedNode.name} becomes unavailable.</p>
        <p className="mt-1 text-theme-text-muted">Based on the connections shown in this map, including indirect dependencies. This is a planning aid, not a live failure diagnosis.</p>
        {paths.size === 0 ? <p className="mt-2">No dependent services are shown in this map.</p> : <ul aria-label="Affected services" className="mt-2 space-y-2">
          {[...paths].map(([id, path]) => <li key={id}><span className="font-semibold">{names.get(id)}</span><span className="ml-2 text-theme-text-muted">{path.map(part => names.get(part) || part).join(' → ')}</span></li>)}
        </ul>}
      </div>}
    </section>
  )
}
