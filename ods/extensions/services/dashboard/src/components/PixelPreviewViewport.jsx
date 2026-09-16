import {useState} from 'react'
import './pixel-preview-viewport.css'

const sizes = {phone:[375,667], tablet:[768,1024], desktop:[1280,800]}
export default function PixelPreviewViewport({access, title, hidden}) {
  const [size, setSize] = useState('fit')
  const [rotated, setRotated] = useState(false)
  const dimensions = sizes[size]
  const [width, height] = dimensions ? (rotated ? [...dimensions].reverse() : dimensions) : ['100%', '100%']
  return <section className="pixel-preview-viewport" hidden={hidden} aria-label="Preview viewport">
    <div className="pixel-viewport-controls">
      <label>Viewport <select aria-label="Preview viewport size" value={size} onChange={event => {setSize(event.target.value); setRotated(false)}}><option value="fit">Fit panel</option><option value="phone">Phone · 375 × 667</option><option value="tablet">Tablet · 768 × 1024</option><option value="desktop">Desktop · 1280 × 800</option></select></label>
      <button type="button" disabled={!dimensions} aria-pressed={rotated} onClick={() => setRotated(value => !value)}>Rotate viewport</button>
      {dimensions && <span role="status">{width} × {height} CSS pixels</span>}
    </div>
    <div className="pixel-viewport-stage">
      <iframe src={access.frameUrl} title={title} hidden={hidden} sandbox={access.sandbox} data-preview-route={access.route} referrerPolicy="no-referrer" style={{width,height}}/>
    </div>
    {dimensions && <p className="pixel-viewport-note">Layout size only. Browser and touch behavior stay the same; scroll to inspect the full viewport.</p>}
  </section>
}
