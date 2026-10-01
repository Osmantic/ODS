import {useDashboardSession} from '../DashboardSignInGate'
import {useEffect, useRef, useState} from 'react'
import {Upload, Trash2} from 'lucide-react'
import {prepareProfilePhoto, saveProfile, useLocalProfile} from '../../lib/localProfile'
import UserAvatar from '../UserAvatar'
import MetalMetricIcon from '../MetalMetricIcon'
import {usePortalIdentity} from '../../contexts/PortalIdentityContext'
import {useI18n} from '../../i18n'
import LanguageSelector from '../../i18n/LanguageSelector'

export default function ProfileSettings() {
  const {t} = useI18n()
  const {session, changePassword, signOut} = useDashboardSession()
  const {displayName} = usePortalIdentity()
  const saved = useLocalProfile()
  const [draft,setDraft] = useState(saved)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const input = useRef(null)
  const selection = useRef(0)
  const previousSaved = useRef(saved)
  useEffect(() => {
    const previous = previousSaved.current
    previousSaved.current = saved
    // Sync untouched fields without replacing edits made in this form.
    setDraft(current => ({
      name: current.name === previous.name ? saved.name : current.name,
      photo: current.photo === previous.photo ? saved.photo : current.photo,
    }))
  },[saved])
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
    try {setDraft(saveProfile(draft, saved));setError('');setNotice(t('profile.saved'))}
    catch {setError(t('profile.saveError'));setNotice('')}
  }
  return <form className="profile-settings" aria-label={t('profile.aria')} onSubmit={submit}>
    <h2>{t('profile.identity')}</h2><p>{t('profile.identityBody', {displayName})}</p>
    <LanguageSelector />
    <div className="profile-photo-editor"><UserAvatar profile={draft}/><div>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" aria-label={t('profile.photo')} onChange={upload} hidden/>
      <button type="button" onClick={() => input.current?.click()} disabled={busy}><MetalMetricIcon icon={Upload} size={14}/>{busy ? t('profile.preparePhoto') : t('profile.uploadPhoto')}</button>
      {draft.photo && <button type="button" onClick={() => {selection.current++;setBusy(false);setDraft(current=>({...current,photo:''}));setNotice('')}}><Trash2 size={13}/>{t('profile.removePhoto')}</button>}
      <small>{t('profile.photoHelp')}<br/>{t('profile.photoHelp2')}</small>
    </div></div>
    <label className="profile-name-label">{t('profile.displayName')}<input name="displayName" autoComplete="nickname" maxLength={60} placeholder={t('profile.displayNamePlaceholder')} value={draft.name} onChange={event => {setDraft(current=>({...current,name:event.target.value}));setNotice('')}}/></label>
    <p className="profile-privacy">{t('profile.privacy')}</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="profile-settings-actions"><button type="submit" disabled={busy}>{t('profile.save')}</button></div>
    <section className="profile-dashboard-access" aria-label={t('profile.dashboardAccess')}>
      <h3>{t('profile.dashboardAccess')}</h3>
      <div className="profile-settings-actions">
        <button type="button" onClick={changePassword}>{t('profile.changePassword')}</button>
        {session && <button type="button" onClick={signOut}>{t('profile.signOut')}</button>}
      </div>
    </section>
  </form>
}
