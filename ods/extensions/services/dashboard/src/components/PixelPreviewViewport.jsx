import {useState} from 'react'
import './pixel-preview-viewport.css'

const sizes = {phone:[375,667], tablet:[768,1024], desktop:[1280,800]}
export default function PixelPreviewViewport({access, title, hidden}) {
  const [size, setSize] = useState('fit')
  const [rotated, setRotated] = useState(false)
  const [custom, setCustom] = useState([1024,768])
  const [draft, setDraft] = useState(['1024','768'])
  const valid = draft.every(value => /^\d+$/.test(value) && Number(value) >= 240 && Number(value) <= 4096)
  const dimensions = size === 'custom' ? custom : sizes[size]
  const [width, height] = dimensions ? (rotated ? [...dimensions].reverse() : dimensions) : ['100%', '100%']
  return <section className="pixel-preview-viewport" hidden={hidden} aria-label="Preview viewport">
    <div className="pixel-viewport-controls">
      <label>Viewport <select aria-label="Preview viewport size" value={size} onChange={event => {setSize(event.target.value); setRotated(false)}}><option value="fit">Fit panel</option><option value="phone">Phone · 375 × 667</option><option value="tablet">Tablet · 768 × 1024</option><option value="desktop">Desktop · 1280 × 800</option><option value="custom">Custom dimensions</option></select></label>
      {size === 'custom' && <form onSubmit={event => {event.preventDefault(); if (valid) {setCustom(draft.map(Number)); setRotated(false)}}}>
        {['width','height'].map((label,index) => <label key={label}>Viewport {label} <input aria-label={`Viewport ${label}`} type="number" min="240" max="4096" step="1" value={draft[index]} onChange={event => setDraft(previous => previous.map((value,at) => at === index ? event.target.value : value))}/></label>)}
        <button type="submit" disabled={!valid}>Apply viewport</button>
        <span>240–4096 CSS pixels per side</span>
      </form>}
      <button type="button" disabled={!dimensions} aria-pressed={rotated} onClick={() => setRotated(value => !value)}>Rotate viewport</button>
      {dimensions && <span role="status">{width} × {height} CSS pixels</span>}
    </div>
    <div className="pixel-viewport-stage">
      <iframe src={access.frameUrl} title={title} hidden={hidden} sandbox={access.sandbox} data-preview-route={access.route} referrerPolicy="no-referrer" style={{width,height}}/>
    </div>
    {dimensions && <p className="pixel-viewport-note">Layout size only. Browser and touch behavior stay the same; scroll to inspect the full viewport.</p>}
  </section>
}
