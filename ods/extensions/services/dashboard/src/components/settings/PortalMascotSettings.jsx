import {useState} from 'react'
import PixelMascot from '../PixelMascot'
import {PORTAL_MASCOT_DEFAULTS, saveMascotPreferences, useMascotPreferences} from '../../lib/portalMascotPreferences'
import './portal-mascot-settings.css'

const states = [
  ['idle','Idle','Breathing, curious glances and blinks. Move the pointer or click Portal to interact.'],
  ['thinking','Thinking','A question mark and a small magnifying glass alternate as visual thinking expressions.'],
  ['working','Working','A focused bounce and a turning gear while the runtime reports tool activity.'],
  ['waiting','Waiting','A turning hourglass with a moving grain of sand. A waiting expression, not a progress estimate.'],
  ['blocked','Attention','A small head tilt, a soft concerned expression and a discreet attention mark. No angry face or flashing alarm.'],
  ['done','Done','One happy bounce, then smiling eyes at rest.'],
  ['sleeping','Sleeping','Soft breathing, curved closed eyes and floating zzz. Typing in chat or clicking Portal wakes the mascot with a stretch.'],
]
export default function PortalMascotSettings() {
  const preferences = useMascotPreferences()
  const [state,setState] = useState('thinking')
  const [replay,setReplay] = useState(0)
  const [error,setError] = useState('')
  function update(patch) {setError(saveMascotPreferences(patch) ? '' : 'Could not save preferences in this browser.')}
  return <section className="portal-mascot-settings" aria-labelledby="portal-mascot-title">
    <h2 id="portal-mascot-title">Portal mascot</h2>
    <p>Customize the little character without changing the assistant or its permissions. Saved in this browser.</p>
    <label className="portal-mascot-setting"><span><strong>Show mascot</strong><small>Hide or restore Portal’s character across the interface.</small></span><input type="checkbox" checked={preferences.enabled} onChange={event=>update({enabled:event.target.checked})}/></label>
    <label className="portal-mascot-setting"><span><strong>Animate mascot</strong><small>Turn motion off and keep static expressions. System reduced-motion preferences are always respected.</small></span><input type="checkbox" checked={preferences.animated} onChange={event=>update({animated:event.target.checked})}/></label>
    <label className="portal-mascot-setting"><span><strong>Sleep after inactivity</strong><small>Time without typing in chat or clicking Portal. Moving the mouse or using other pages does not reset it; running tasks never sleep.</small></span><select aria-label="Sleep after inactivity" value={preferences.sleepAfterSeconds} onChange={event=>update({sleepAfterSeconds:Number(event.target.value)})}>
      <option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="120">2 minutes</option><option value="300">5 minutes</option><option value="0">Never</option>
    </select></label>
    <div className="portal-mascot-demo">
      <PixelMascot key={replay} preview interactive state={state} className="portal-settings-character"/>
      <div><h3>Try the expressions</h3><p>{states.find(([id])=>id===state)[2]}</p><small>Demo only — not live task activity. The preview remains visible when the chat mascot is hidden.</small></div>
    </div>
    <div className="portal-mascot-states" aria-label="Preview mascot state">{states.map(([id,label])=><button key={id} type="button" aria-pressed={state===id} onClick={()=>{setState(id);setReplay(value=>value+1)}}>{label}</button>)}</div>
    <p>In Idle, clicks cycle through left/right hops, a squish, a wink and a little dance. Three quick clicks reveal a sparkle surprise. Waking brings a stretch.</p>
    <button className="portal-mascot-reset" type="button" onClick={()=>update(PORTAL_MASCOT_DEFAULTS)}>Reset mascot preferences</button>
    {error && <p role="alert">{error}</p>}
  </section>
}
