// Required settings for the Extensions page install/retry dialog.
//
// Uses the same backend as the Portal's extension setup: the install plan
// reports which declared settings are missing (presence only, never values)
// and POST /api/extensions/{id}/configure writes them to the host's ODS
// settings through the host agent. The install/enable endpoints refuse with
// the same field list, so a refusal can be answered in the same dialog.

const SERVICE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/
const SETTING_KEY = /^[A-Z][A-Z0-9_]{0,127}$/

function settingField(field) {
  if (!field || !SETTING_KEY.test(field.key) || typeof field.secret !== 'boolean') return null
  return {
    key: field.key,
    secret: field.secret,
    description: typeof field.description === 'string' ? field.description.slice(0, 500) : '',
  }
}

function uniqueFields(fields) {
  if (!Array.isArray(fields) || fields.length > 128) return null
  const parsed = fields.map(settingField)
  if (parsed.some(field => !field) || new Set(parsed.map(field => field.key)).size !== parsed.length) return null
  return parsed
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
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Settings could not be saved.')
  }
  const receipt = await response.json()
  if (receipt?.service_id !== serviceId || receipt.status !== 'saved' || !Array.isArray(receipt.saved_keys) ||
      JSON.stringify([...receipt.saved_keys].sort()) !== JSON.stringify(keys)) {
    throw new Error('Settings could not be confirmed as saved.')
  }
}

export function ExtensionSettingsFields({ fields, values, onChange, disabled }) {
  return (
    <fieldset disabled={disabled} className="mb-5 space-y-3" aria-label="Required settings">
      <legend className="mb-1 text-[10px] font-mono uppercase tracking-[0.16em] text-theme-text-muted/70">Required settings</legend>
      <p className="text-[11px] leading-relaxed text-theme-text-muted/70">
        Saved to this machine&apos;s ODS settings (.env). Secret values are not shown again; record any
        password you will need to sign in. Details &rarr; Configured Credentials shows how to read them later.
      </p>
      {fields.map(field => (
        <label key={field.key} className="block">
          <span className="flex items-center gap-2 font-mono text-[11px] text-theme-accent-light">
            {field.key}
            {field.secret && <span className="rounded-full border border-theme-border px-1.5 text-[9px] uppercase tracking-[0.12em] text-theme-text-muted">secret</span>}
          </span>
          {field.description && <span className="mt-0.5 block text-[11px] leading-relaxed text-theme-text-muted/70">{field.description}</span>}
          <input
            type={field.secret ? 'password' : 'text'}
            required
            maxLength={4096}
            spellCheck={false}
            autoComplete={field.secret ? 'new-password' : 'off'}
            value={values[field.key] || ''}
            onChange={event => onChange(field.key, event.target.value)}
            className="mt-1.5 w-full rounded-lg border border-theme-border bg-theme-bg/40 px-3 py-2 font-mono text-xs text-theme-text outline-none placeholder:text-theme-text-muted/55 focus:border-theme-accent/60"
          />
        </label>
      ))}
    </fieldset>
  )
}
