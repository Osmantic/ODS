import {useEffect, useState} from 'react'

export const PROFILE_KEY = 'ods.local-profile.v1'
const EVENT = 'ods:profile-changed'
const MAX_AVATAR_LENGTH = 200000
export function normalizeProfile(value) {
  return {
    name: typeof value?.name === 'string' ? value.name.trim().slice(0,60) : '',
    photo: typeof value?.photo === 'string' && value.photo.length <= MAX_AVATAR_LENGTH && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(value.photo) ? value.photo : '',
  }
}
export function readProfile() {
  try {return normalizeProfile(JSON.parse(localStorage.getItem(PROFILE_KEY) || '{}'))}
  catch {return normalizeProfile({})}
}
export function saveProfile(value, previous = null) {
  const profile = normalizeProfile(value)
  if (previous) {
    // Storage events are queued across tabs. Preserve already-committed values
    // for fields this form did not edit, even before that event is delivered.
    const current = readProfile()
    for (const field of ['name', 'photo']) {
      if (profile[field] === previous[field]) profile[field] = current[field]
    }
  }
  // Never silently report success if browser storage is unavailable/full.
  localStorage.setItem(PROFILE_KEY, JSON.stringify(profile))
  window.dispatchEvent(new Event(EVENT))
  return profile
}
export function useLocalProfile() {
  const [profile,setProfile] = useState(readProfile)
  useEffect(() => {
    const refresh = event => {if (event.type === EVENT || !event.key || event.key === PROFILE_KEY) setProfile(readProfile())}
    window.addEventListener(EVENT,refresh)
    window.addEventListener('storage',refresh)
    return () => {window.removeEventListener(EVENT,refresh);window.removeEventListener('storage',refresh)}
  },[])
  return profile
}

export async function prepareProfilePhoto(file) {
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error('Choose a JPG, PNG or WebP image.')
  if (file.size > 5 * 1024 * 1024) throw new Error('Choose an image smaller than 5 MB.')
  let bitmap
  try {bitmap = await createImageBitmap(file, {imageOrientation:'from-image'})}
  catch {throw new Error('This image could not be opened. Try another photo.')}
  try {
    if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 40000000) throw new Error('This image is too large. Choose a smaller photo.')
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 256
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Photo processing is unavailable in this browser.')
    const side = Math.min(bitmap.width,bitmap.height)
    context.drawImage(bitmap,(bitmap.width-side)/2,(bitmap.height-side)/2,side,side,0,0,256,256)
    const photo = canvas.toDataURL('image/webp',.86)
    if (!normalizeProfile({photo}).photo) throw new Error('The photo could not be resized. Try another image.')
    return photo
  } finally {bitmap.close()}
}
