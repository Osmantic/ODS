import {useMemo, useSyncExternalStore} from 'react'

export const PORTAL_MASCOT_KEY = 'ods.portal.mascot.v1'
export const PORTAL_MASCOT_DEFAULTS = Object.freeze({enabled:true, animated:true, sleepAfterSeconds:60})
const eventName = 'ods-portal-mascot-preferences'
const delays = [0,15,30,60,120,300]
export function normalizeMascotPreferences(value) {
  return {
    enabled: typeof value?.enabled === 'boolean' ? value.enabled : true,
    animated: typeof value?.animated === 'boolean' ? value.animated : true,
    sleepAfterSeconds: delays.includes(value?.sleepAfterSeconds) ? value.sleepAfterSeconds : 60,
  }
}
function read() { try {return localStorage.getItem(PORTAL_MASCOT_KEY)} catch {return null} }
function parse(raw) { try {return normalizeMascotPreferences(JSON.parse(raw))} catch {return PORTAL_MASCOT_DEFAULTS} }
function subscribe(notify) {
  const storage = event => {if (!event.key || event.key === PORTAL_MASCOT_KEY) notify()}
  window.addEventListener('storage',storage)
  window.addEventListener(eventName,notify)
  return () => {window.removeEventListener('storage',storage);window.removeEventListener(eventName,notify)}
}
export function saveMascotPreferences(patch) {
  try {
    localStorage.setItem(PORTAL_MASCOT_KEY,JSON.stringify(normalizeMascotPreferences({...parse(read()),...patch})))
    window.dispatchEvent(new Event(eventName))
    return true
  } catch {return false}
}
export function useMascotPreferences() {
  const raw = useSyncExternalStore(subscribe,read,()=>null)
  return useMemo(()=>parse(raw),[raw])
}
