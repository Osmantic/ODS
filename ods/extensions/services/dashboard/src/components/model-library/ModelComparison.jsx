import { useState } from 'react'

const value = (number, unit) => Number.isFinite(number) && number > 0 ? `${number.toLocaleString()} ${unit}` : 'Not reported'

export default function ModelComparison({ models }) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(['', '', ''])
  const compared = selected.map(id => models.find(model => model.id === id)).filter(Boolean)
  const rows = [
    ['Download size', model => model.size || value(model.sizeGb, 'GB')],
    ['Estimated VRAM', model => value(model.estimatedRequired ?? model.vramRequired, 'GB')],
    ['Catalog context', model => value(model.contextLength, 'tokens')],
    ['Reported speed', model => value(model.tokensPerSec, 'tokens/s')],
    ['Quantization', model => model.quantization || 'Not reported'],
    ['VRAM fit', model => model.fitsVram === true ? 'Fits' : model.fitsVram === false ? 'Does not fit' : 'Not reported'],
  ]
  return (
    <section className="mb-4 rounded-xl border border-theme-border bg-theme-card p-4">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="font-semibold text-theme-text">Compare models</button>
      {open && <>
      <p className="my-3 text-sm text-theme-text-muted">Compare up to three catalog models before downloading. Estimates do not guarantee runtime compatibility.</p>
      <div className="mb-4 flex flex-wrap gap-3">
        {selected.map((id, index) => (
          <label key={index} className="text-sm text-theme-text">
            Model {index + 1}
            <select aria-label={`Comparison model ${index + 1}`} value={models.some(model => model.id === id) ? id : ''}
              onChange={event => setSelected(previous => previous.map((item, slot) => slot === index ? event.target.value : item))}
              className="ml-2 rounded border border-theme-border bg-theme-card p-2">
              <option value="">Choose a model</option>
              {models.map(model => <option key={model.id} value={model.id} disabled={selected.includes(model.id) && id !== model.id}>{model.name}</option>)}
            </select>
          </label>
        ))}
        <button type="button" onClick={() => setSelected(['', '', ''])} className="text-sm text-theme-accent">Clear comparison</button>
      </div>
      {compared.length < 2 ? <p className="text-sm text-theme-text-muted">Select at least two models to compare.</p> : (
        <div className="overflow-x-auto">
          <table aria-label="Model comparison" className="w-full text-left text-sm text-theme-text">
            <thead><tr><th scope="col" className="p-2">Attribute</th>{compared.map(model => <th scope="col" className="p-2" key={model.id}>{model.name}</th>)}</tr></thead>
            <tbody>{rows.map(([label, read]) => <tr key={label}><th scope="row" className="p-2">{label}</th>{compared.map(model => <td className="p-2" key={model.id}>{read(model)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      </>}
    </section>
  )
}
