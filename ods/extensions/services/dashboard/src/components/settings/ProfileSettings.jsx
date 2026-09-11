import {useEffect, useRef, useState} from 'react'
import {Upload, Trash2} from 'lucide-react'
import {prepareProfilePhoto, saveProfile, useLocalProfile} from '../../lib/localProfile'
import UserAvatar from '../UserAvatar'
import MetalMetricIcon from '../MetalMetricIcon'

export default function ProfileSettings() {
  const saved = useLocalProfile()
  const [draft,setDraft] = useState(saved)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const input = useRef(null)
  const selection = useRef(0)
  useEffect(() => {setDraft(saved)},[saved])
  useEffect(() => () => {selection.current++},[])
  async function upload(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const revision = ++selection.current
    setBusy(true);setError('');setNotice('')
    try {
      const photo = await prepareProfilePhoto(file)
      if (revision === selection.current) setDraft(current => ({...current,photo}))
    } catch(error) {if (revision === selection.current) setError(error.message)}
    finally {if (revision === selection.current) setBusy(false)}
  }
  function submit(event) {
    event.preventDefault()
    if (busy) return
    try {setDraft(saveProfile(draft));setError('');setNotice('Profile saved.')}
    catch {setError('Your profile could not be saved. Browser storage may be full or disabled.');setNotice('')}
  }
  return <form className="profile-settings" aria-label="Your profile" onSubmit={submit}>
    <h2>Your identity</h2><p>Your name and photo in the sidebar and Pixel conversations.</p>
    <div className="profile-photo-editor"><UserAvatar profile={draft}/><div>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" aria-label="Profile photo" onChange={upload} hidden/>
      <button type="button" onClick={() => input.current?.click()} disabled={busy}><MetalMetricIcon icon={Upload} size={14}/>{busy ? 'Preparing photo…' : 'Upload photo'}</button>
      {draft.photo && <button type="button" onClick={() => {selection.current++;setBusy(false);setDraft(current=>({...current,photo:''}));setNotice('')}}><Trash2 size={13}/>Remove photo</button>}
      <small>JPG, PNG or WebP · up to 5 MB<br/>Centered crop · circular avatar</small>
    </div></div>
    <label className="profile-name-label">Display name<input name="displayName" autoComplete="nickname" maxLength={60} placeholder="Your name" value={draft.name} onChange={event => {setDraft(current=>({...current,name:event.target.value}));setNotice('')}}/></label>
    <p className="profile-privacy">Saved only in this browser. Your photo is resized locally and is not sent to the AI model. This does not change your login or permissions.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="profile-settings-actions"><button type="submit" disabled={busy}>Save profile</button></div>
  </form>
}
