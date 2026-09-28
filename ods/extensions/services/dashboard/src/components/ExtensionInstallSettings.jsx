// Required settings for the Extensions page install/retry dialog.
//
// Uses the same backend as the Portal's extension setup: the install plan
// reports which declared settings are missing (presence only, never values)
// and POST /api/extensions/{id}/configure writes them to the host's ODS
// settings through the host agent. The install/enable endpoints refuse with
// the same field list, so a refusal can be answered in the same dialog.
//
// A declared setting may carry its expected format (from the manifest's
// env_vars: format, pattern, min_length, max_length, generate). The dialog
// shows it, checks values before saving and can fill a random value. The API
// checks the same format again before anything is written.

import { useState } from 'react'

const SERVICE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/
const SETTING_KEY = /^[A-Z][A-Z0-9_]{0,127}$/
const ALPHANUMERIC = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
const MAX_VALUE_BYTES = 4096

function randomBytes(count) {
  const bytes = new Uint8Array(count)
  globalThis.crypto.getRandomValues(bytes)
  return bytes
}

const randomHex = length => Array.from(randomBytes(length / 2), byte => byte.toString(16).padStart(2, '0')).join('')

function randomAlphanumeric(length) {
  let value = ''
  while (value.length < length) {
    // 248 = 4 × 62: discarding larger bytes keeps every character equally likely.
    for (const byte of randomBytes(length * 2)) {
      if (byte < 248 && value.length < length) value += ALPHANUMERIC[byte % 62]
    }
  }
  return value
}

const GENERATORS = {
  hex32: () => randomHex(32),
  hex64: () => randomHex(64),
  hex128: () => randomHex(128),
  password: () => randomAlphanumeric(24),
  token: () => randomAlphanumeric(48),
}

const lengthLimit = value => (value === null || value === undefined ? null
  : Number.isInteger(value) && value >= 1 && value <= MAX_VALUE_BYTES ? value : undefined)

// The API's normalized format, or null when unconstrained or unusable here
// (the API still enforces it when the value is saved).
function settingFormat(format) {
  if (!format || typeof format !== 'object' || typeof format.hint !== 'string') return null
  if (!Array.isArray(format.patterns) || format.patterns.length > 4) return null
  const minLength = lengthLimit(format.minLength)
  const maxLength = lengthLimit(format.maxLength)
  if (minLength === undefined || maxLength === undefined) return null
  let patterns
  try {
    patterns = format.patterns.map(pattern => {
      if (typeof pattern !== 'string' || pattern.length > 600 || !pattern.startsWith('^') || !pattern.endsWith('$')) {
        throw new Error('pattern')
      }
      return new RegExp(pattern)
    })
  } catch {
    return null
  }
  const generate = Object.hasOwn(GENERATORS, format.generate || '') ? format.generate : null
  const distinctFrom = Array.isArray(format.distinctFrom)
    ? format.distinctFrom.filter(key => typeof key === 'string' && SETTING_KEY.test(key)).slice(0, 8) : []
  return { hint: format.hint.slice(0, 300), patterns, minLength, maxLength, generate, distinctFrom }
}

function settingField(field) {
  if (!field || !SETTING_KEY.test(field.key) || typeof field.secret !== 'boolean') return null
  return {
    key: field.key,
    secret: field.secret,
    description: typeof field.description === 'string' ? field.description.slice(0, 500) : '',
    format: settingFormat(field.format),
  }
}

function uniqueFields(fields) {
  if (!Array.isArray(fields) || fields.length > 128) return null
  const parsed = fields.map(settingField)
  if (parsed.some(field => !field) || new Set(parsed.map(field => field.key)).size !== parsed.length) return null
  return parsed
}

// Minimum in characters and maximum in UTF-8 bytes, as the API counts them.
function conformsFormat(format, value) {
  if (!format) return true
  if (format.minLength && [...value].length < format.minLength) return false
  if (format.maxLength && new TextEncoder().encode(value).length > format.maxLength) return false
  return format.patterns.every(pattern => pattern.test(value))
}

// '' when the value can be saved; otherwise what the owner should enter.
// ``values`` holds the dialog's other values for "must differ" settings.
export function settingProblem(field, value, values = {}) {
  if (!value || !value.trim()) return 'Enter a value.'
  if ([...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
    return 'Use a single line without control characters.'
  }
  if (new TextEncoder().encode(value).length > MAX_VALUE_BYTES) return 'This value is too long.'
  if (!conformsFormat(field.format, value)) return `Expected ${field.format.hint}.`
  const same = (field.format?.distinctFrom || []).find(other => values[other] === value)
  return same ? `Must differ from ${same}.` : ''
}

export const canGenerate = field => Boolean(field.format?.generate) &&
  typeof globalThis.crypto?.getRandomValues === 'function'

// A crypto-random value in the declared format, or null. Candidates that miss
// an extra rule (for example "an uppercase letter and a digit") are redrawn.
export function generateSettingValue(field) {
  if (!canGenerate(field)) return null
  for (let attempt = 0; attempt < 64; attempt += 1) {
    const value = GENERATORS[field.format.generate]()
    if (conformsFormat(field.format, value)) return value
  }
  return null
}

// Settings the owner must enter before a fresh install, from the install plan.
// An extension with a setup hook generates the settings it declares while it
// installs, so nothing is asked for there (the API applies the same rule).
export function installPlanSettings(plan, serviceId) {
  if (plan?.schemaVersion !== 1 || plan.extensionId !== serviceId || !Array.isArray(plan.steps)) return null
  const step = plan.steps.find(item => item?.extensionId === serviceId)
  if (!step || !Array.isArray(step.missingConfiguration) || !Array.isArray(step.configuration)) return null
  if (step.setupHook === true) return []
  const fields = step.missingConfiguration.map(key => {
    const matches = step.configuration.filter(field => field?.key === key)
    return matches.length === 1 && matches[0].required === true && matches[0].configured === false
      ? matches[0] : null
  })
  return fields.some(field => !field) ? null : uniqueFields(fields)
}

// Saved settings whose value does not match the declared format (names only,
// never values). A warning, not a refusal: ODS never changes saved settings,
// and an uninstall keeps them and the extension's data volumes.
export function installPlanWarnings(plan, serviceId) {
  if (plan?.schemaVersion !== 1 || plan.extensionId !== serviceId || !Array.isArray(plan.steps)) return []
  const step = plan.steps.find(item => item?.extensionId === serviceId)
  const keys = Array.isArray(step?.savedConfigurationWarnings) ? step.savedConfigurationWarnings : []
  return keys.filter(key => typeof key === 'string' && SETTING_KEY.test(key)).slice(0, 128)
}

export function savedSettingsWarning(name, keys) {
  if (!keys.length) return ''
  const several = keys.length > 1
  return `The saved ${several ? 'settings' : 'setting'} ${keys.join(', ')} ${several ? 'do' : 'does'} not have the ` +
    `format ${name} requires. ODS keeps saved settings unchanged, and an earlier installation's data volumes ` +
    'are kept too and may still expect the saved value, so the installation may not start; if it fails, ' +
    'its error shows the service’s own reason.'
}

// The install/enable refusal: {code: 'missing_configuration', service_id, message, configuration}.
export function missingSettingsRefusal(detail) {
  if (detail?.code !== 'missing_configuration' || !SERVICE_ID.test(detail.service_id || '')) return null
  const fields = uniqueFields(detail.configuration)
  if (!fields?.length) return null
  return {
    serviceId: detail.service_id,
    fields,
    message: typeof detail.message === 'string' ? detail.message : '',
  }
}

// Write-only: values go to ODS settings and are never read back or logged.
export async function saveExtensionSettings(serviceId, values, signal) {
  const keys = Object.keys(values).sort()
  const response = await fetch(`/api/extensions/${serviceId}/configure`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }), signal, cache: 'no-store',
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    // A 422 names each setting and its expected format, never a value.
    const detail = body.detail?.code === 'invalid_configuration' ? body.detail.message : body.detail
    throw new Error(typeof detail === 'string' ? detail.slice(0, 1000) : 'Settings could not be saved.')
  }
  const receipt = await response.json()
  if (receipt?.service_id !== serviceId || receipt.status !== 'saved' || !Array.isArray(receipt.saved_keys) ||
      JSON.stringify([...receipt.saved_keys].sort()) !== JSON.stringify(keys)) {
    throw new Error('Settings could not be confirmed as saved.')
  }
}

const smallButton = 'rounded-md border border-theme-border px-2 py-0.5 text-[9px] font-mono uppercase tracking-[0.12em] text-theme-text-muted transition-colors hover:border-theme-accent/60 hover:text-theme-text disabled:opacity-50'

export function ExtensionSettingsFields({ fields, values, onChange, disabled }) {
  const [revealed, setRevealed] = useState({})
  const generatable = fields.filter(canGenerate)
  const fill = field => {
    const value = generateSettingValue(field)
    if (value) onChange(field.key, value)
  }
  return (
    <fieldset disabled={disabled} className="mb-5 space-y-3" aria-label="Required settings">
      <legend className="mb-1 flex w-full items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-[0.16em] text-theme-text-muted/70">
        Required settings
        {generatable.length > 0 && generatable.length === fields.length && (
          <button type="button" onClick={() => fields.forEach(fill)} className={smallButton}>Generate all</button>
        )}
      </legend>
      <p className="text-[11px] leading-relaxed text-theme-text-muted/70">
        Saved to this machine&apos;s ODS settings (.env). Secret values are not shown again; record any
        password you will need to sign in. Details &rarr; Configured Credentials shows how to read them later.
        {generatable.length > 0 && ' Generate fills a random value in the required format; use Show to copy it first.'}
      </p>
      {fields.map(field => {
        const value = values[field.key] || ''
        const problem = value ? settingProblem(field, value, values) : ''
        const hintId = `setting-${field.key}-format`
        const problemId = `setting-${field.key}-problem`
        const shown = field.secret && revealed[field.key]
        return (
          <div key={field.key}>
            <div className="flex items-center gap-2">
              <label htmlFor={`setting-${field.key}`} className="flex items-center gap-2 font-mono text-[11px] text-theme-accent-light">
                {field.key}
                {field.secret && <span className="rounded-full border border-theme-border px-1.5 text-[9px] uppercase tracking-[0.12em] text-theme-text-muted">secret</span>}
              </label>
              <span className="ml-auto flex gap-1.5">
                {field.secret && (
                  <button type="button" className={smallButton} aria-label={`${shown ? 'Hide' : 'Show'} ${field.key}`}
                    onClick={() => setRevealed(current => ({ ...current, [field.key]: !current[field.key] }))}>
                    {shown ? 'Hide' : 'Show'}
                  </button>
                )}
                {canGenerate(field) && (
                  <button type="button" className={smallButton} aria-label={`Generate ${field.key}`} onClick={() => fill(field)}>
                    Generate
                  </button>
                )}
              </span>
            </div>
            {field.description && <span className="mt-0.5 block text-[11px] leading-relaxed text-theme-text-muted/70">{field.description}</span>}
            {field.format?.hint && (
              <span id={hintId} className="mt-0.5 block text-[11px] leading-relaxed text-theme-text-muted/70">
                Format: {field.format.hint}
              </span>
            )}
            <input
              id={`setting-${field.key}`}
              type={field.secret && !shown ? 'password' : 'text'}
              required
              maxLength={MAX_VALUE_BYTES}
              spellCheck={false}
              autoComplete={field.secret ? 'new-password' : 'off'}
              value={value}
              aria-invalid={problem ? 'true' : undefined}
              aria-describedby={[field.format?.hint && hintId, problem && problemId].filter(Boolean).join(' ') || undefined}
              onChange={event => onChange(field.key, event.target.value)}
              className={`mt-1.5 w-full rounded-lg border bg-theme-bg/40 px-3 py-2 font-mono text-xs text-theme-text outline-none placeholder:text-theme-text-muted/55 focus:border-theme-accent/60 ${
                problem ? 'border-red-400/60' : 'border-theme-border'}`}
            />
            {problem && <span id={problemId} className="mt-1 block text-[11px] leading-relaxed text-red-300">{problem}</span>}
          </div>
        )
      })}
    </fieldset>
  )
}
