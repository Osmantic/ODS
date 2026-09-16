import {useState, useEffect} from 'react'
import {getSystemRules, saveSystemRules} from '../../lib/pixelSystemRules'

export default function PixelSystemRulesSettings() {
  const [draft, setDraft] = useState(() => getSystemRules())
  const [saved, setSaved] = useState(false)

  function save(event) {
    event.preventDefault()
    saveSystemRules(draft)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return <section className="pixel-system-rules-settings" aria-labelledby="pixel-rules-title">
    <h2 id="pixel-rules-title">Custom Instructions</h2>
    <p>These global instructions are automatically injected into every conversation with Pixel. Saved in this browser.</p>
    <form onSubmit={save} className="pixel-form" style={{marginTop: '1rem'}}>
      <label>
        <span style={{display: 'block', fontWeight: 600, marginBottom: '0.25rem'}}>System Rules</span>
        <textarea 
          value={draft} 
          onChange={e => {setDraft(e.target.value); setSaved(false)}} 
          rows={6} 
          maxLength={100000} 
          placeholder="e.g. Always write tests in Pytest. Never use classes." 
          style={{width: '100%', minHeight: '120px', padding: '12px', boxSizing: 'border-box', fontFamily: 'var(--font-mono, monospace)', background: 'var(--bg-input, rgba(0,0,0,0.2))', color: 'inherit', border: '1px solid var(--border-color, #444)', borderRadius: '6px'}}
        />
      </label>
      <div style={{marginTop: '1rem'}}>
        <button type="submit" className="btn-primary" disabled={draft === getSystemRules()}>
           {saved ? 'Saved' : 'Save rules'}
        </button>
      </div>
    </form>
  </section>
}
