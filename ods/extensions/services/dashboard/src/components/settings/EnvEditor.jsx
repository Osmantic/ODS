import { Search, RefreshCw, Save, Eye, EyeOff, Check, Trash2, Undo2 } from 'lucide-react'

export default function EnvEditor({
  editor,
  search,
  onSearchChange,
  sections,
  activeSection,
  onSectionChange,
  fields,
  values,
  issues,
  issueMap,
  revealedSecrets,
  clearedSecrets = [],
  onToggleReveal,
  onClearSecret = () => {},
  onFieldChange,
  onReload,
  onSave,
  onApply = () => {},
  dirty,
  saving,
  applyPlan = null,
  followUpPlan = null,
  onCompleteFollowUp = () => {},
  applying = false,
}) {
  const activeKeys = activeSection?.keys || []
  const canApply = Boolean(applyPlan?.supported && applyPlan?.services?.length > 0 && editor?.agentAvailable !== false)

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-white/8 bg-theme-card px-4 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-accent-light">
              Local configuration
            </p>
            <p className="mt-1 text-sm text-theme-text">
              Edit the ODS `.env` directly from the dashboard.
            </p>
            <p className="mt-2 break-all font-mono text-[11px] text-theme-text-muted">
              {editor.path}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <ToolbarButton icon={RefreshCw} label="Reload" onClick={onReload} />
            <ToolbarButton icon={Save} label={saving ? 'Saving...' : 'Save .env'} onClick={onSave} primary disabled={!dirty || saving} />
            <ToolbarButton
              icon={RefreshCw}
              label={applying ? 'Applying...' : 'Apply changes'}
              onClick={onApply}
              disabled={!canApply || applying || saving}
            />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Chip>{Object.keys(fields || {}).length} fields</Chip>
          <Chip>{issues.length} validation issue{issues.length === 1 ? '' : 's'}</Chip>
          {editor.backupPath ? <Chip accent>last backup {editor.backupPath}</Chip> : null}
          {applyPlan?.status && applyPlan.status !== 'none' ? <Chip accent>{applyPlan.status}</Chip> : null}
        </div>
      </div>

      {applyPlan?.status && applyPlan.status !== 'none' ? (
        <div className="rounded-xl border border-theme-accent/20 bg-theme-accent/10 px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-accent-light">
            {applyPlan.status === 'post-apply' ? 'Required follow-up' : 'Pending runtime changes'}
          </p>
          <p className="mt-1 text-sm text-theme-text">{applyPlan.summary}</p>
          {applyPlan.postApplyActions?.map((action) => (
            <div key={action.id} className="mt-3 border-t border-theme-accent/15 pt-3">
              <p className="text-xs font-semibold text-theme-text">{action.title}</p>
              <p className="mt-1 text-xs text-theme-text-muted">{action.message}</p>
            </div>
          ))}
        </div>
      ) : null}

      {followUpPlan ? (
        <div className="rounded-xl border border-theme-accent/20 bg-theme-accent/10 px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-accent-light">
                Required follow-up
              </p>
              <p className="mt-1 text-sm text-theme-text">{followUpPlan.summary}</p>
            </div>
            <ToolbarButton icon={Check} label="Mark complete" onClick={onCompleteFollowUp} />
          </div>
          {followUpPlan.postApplyActions?.map((action) => (
            <div key={action.id} className="mt-3 border-t border-theme-accent/15 pt-3">
              <p className="text-xs font-semibold text-theme-text">{action.title}</p>
              <p className="mt-1 text-xs text-theme-text-muted">{action.message}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-3">
        <Hint title="Save behavior" text={editor.saveHint} />
        <Hint title="Restart behavior" text={editor.restartHint} />
        <Hint
          title="Apply behavior"
          text={
            editor?.agentAvailable === false
              ? 'ODS host agent is offline. Start it first, then use Apply changes to recreate affected services.'
              : canApply
                ? `Apply changes will recreate: ${applyPlan.services.join(', ')}.`
                : 'Apply changes becomes available after saving keys that affect running services.'
          }
        />
      </div>

      {issues.length > 0 ? (
        <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/10 px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-yellow-100">Validation notes</p>
          <div className="mt-2 space-y-1">
            {issues.slice(0, 8).map((issue, index) => (
              <p key={`${issue.key || 'line'}-${index}`} className="text-sm text-yellow-50/90">
                {issue.key ? `${issue.key}: ` : ''}{issue.message}
              </p>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[240px_1fr]">
        <div className="self-start rounded-2xl border border-white/8 bg-theme-card px-3 py-3 xl:sticky xl:top-6">
          <label className="flex items-center gap-2 rounded-xl border border-white/8 bg-black/[0.16] px-3 py-2">
            <span className="sr-only">Filter configuration fields</span>
            <Search size={14} className="text-theme-text-muted" />
            <input
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="Filter fields..."
              aria-label="Filter configuration fields"
              className="w-full bg-transparent text-sm text-theme-text outline-none placeholder:text-theme-text-muted/55"
            />
          </label>
          <div className="mt-3 max-h-[60vh] overflow-y-auto pr-1">
            {sections.map((section) => (
              <button
                key={section.id}
                type="button"
                onClick={() => onSectionChange(section.id)}
                aria-pressed={activeSection?.id === section.id}
                className={`group relative flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left transition-colors ${
                  activeSection?.id === section.id
                    ? 'bg-theme-accent/10 text-theme-text'
                    : 'text-theme-text-muted hover:bg-white/[0.04] hover:text-theme-text'
                }`}
              >
                <span
                  className={`absolute bottom-1.5 left-0 top-1.5 w-px rounded-full transition-colors ${
                    activeSection?.id === section.id ? 'bg-theme-accent-light' : 'bg-transparent group-hover:bg-white/10'
                  }`}
                />
                <span className="min-w-0 pl-2">
                  <span className="block truncate text-sm font-medium">{section.title}</span>
                </span>
                <span
                  className={`shrink-0 text-[10px] font-mono uppercase tracking-[0.14em] ${
                    activeSection?.id === section.id ? 'text-theme-accent-light' : 'text-theme-text-muted/55'
                  }`}
                >
                  {section.keys.length}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex h-[30rem] flex-col rounded-2xl border border-white/8 bg-theme-card px-4 py-4">
          {activeSection ? (
            <>
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/60">{activeSection.id}</p>
                  <h3 className="mt-1 text-lg font-semibold text-theme-text">{activeSection.title}</h3>
                </div>
                <Chip>{activeKeys.length} fields</Chip>
              </div>

              <div className="min-h-0 overflow-y-auto pr-1">
                <div className="space-y-3">
                  {activeKeys.map((key) => (
                    <FieldCard
                      key={key}
                      field={fields[key]}
                      value={values[key] ?? ''}
                      issues={issueMap[key] || []}
                      revealed={Boolean(revealedSecrets[key])}
                      cleared={clearedSecrets.includes(key)}
                      onToggleReveal={() => onToggleReveal(key)}
                      onClearSecret={() => onClearSecret(key)}
                      onChange={(value) => onFieldChange(key, value)}
                    />
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="rounded-xl border border-white/8 bg-black/[0.14] px-4 py-6 text-sm text-theme-text-muted">
              No fields match the current filter.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ToolbarButton({ icon: Icon, label, onClick, primary = false, disabled = false }) {
  const cls = primary
    ? 'liquid-metal-button text-white disabled:cursor-default disabled:opacity-50'
    : 'border-white/10 bg-black/[0.16] text-theme-text-muted hover:text-theme-text'
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-full border px-3 py-2 text-[10px] font-mono uppercase tracking-[0.16em] ${cls}`}
    >
      <span className="flex items-center gap-1.5"><Icon size={12} />{label}</span>
    </button>
  )
}

function Chip({ children, accent = false }) {
  return (
    <span className={`rounded-full border px-2.5 py-1 text-[10px] font-mono uppercase tracking-[0.16em] ${
      accent ? 'border-theme-accent/20 bg-theme-accent/10 text-theme-text' : 'border-white/10 bg-black/[0.12] text-theme-text-muted'
    }`}>{children}</span>
  )
}

function Hint({ title, text }) {
  return (
    <div className="rounded-xl border border-white/8 bg-black/[0.14] px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted/60">{title}</p>
      <p className="mt-1 text-sm text-theme-text-muted">{text}</p>
    </div>
  )
}

function FieldCard({ field, value, issues, revealed, cleared, onToggleReveal, onClearSecret, onChange }) {
  const hasIssues = issues.length > 0
  const isEnum = Array.isArray(field?.enum) && field.enum.length > 0
  const isBoolean = field?.type === 'boolean'
  const isInteger = field?.type === 'integer'
  const isReadOnly = Boolean(field?.readOnly)
  const secretPlaceholder = field?.secret ? (field?.hasValue ? 'Stored locally' : 'Not set') : (field?.default !== undefined && field?.default !== null ? String(field.default) : '')

  return (
    <div className={`rounded-2xl border px-4 py-3 ${hasIssues ? 'border-yellow-500/20 bg-yellow-500/5' : 'border-white/8 bg-black/[0.14]'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-theme-text">{field?.label}</p>
            {field?.required ? <Badge>required</Badge> : null}
            {field?.secret ? <Badge muted>{field?.hasValue ? 'stored' : 'secret'}</Badge> : null}
            {isReadOnly ? <Badge muted>read only</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-theme-text-muted">{field?.description || 'No description available.'}</p>
        </div>
        <Badge muted>{field?.key}</Badge>
      </div>

      <div className="mt-3">
        {isBoolean ? (
          <div className="flex items-center rounded-full border border-white/10 bg-black/[0.16] p-1">
            {[
              { id: '', label: 'default' },
              { id: 'true', label: 'true' },
              { id: 'false', label: 'false' },
            ].map((option) => (
              <button
                key={option.label}
                type="button"
                disabled={isReadOnly}
                onClick={() => onChange(option.id)}
                className={`rounded-full px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.16em] transition-colors disabled:cursor-default disabled:opacity-60 ${
                  String(value).toLowerCase() === option.id ? 'bg-theme-accent text-white' : 'text-theme-text-muted hover:text-theme-text'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : isEnum ? (
          <select
            value={value}
            disabled={isReadOnly}
            onChange={(event) => onChange(event.target.value)}
            className="w-full rounded-xl border border-white/8 bg-black/[0.16] px-3 py-2.5 text-sm text-theme-text outline-none focus:border-theme-accent/30 disabled:cursor-default disabled:opacity-70"
          >
            <option value="">Use default</option>
            {field.enum.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        ) : (
          <div className="flex items-center gap-2">
            <input
              type={field?.secret && !revealed ? 'password' : (isInteger ? 'number' : 'text')}
              value={value}
              disabled={isReadOnly}
              onChange={(event) => onChange(event.target.value)}
              placeholder={secretPlaceholder}
              autoComplete="off"
              className="w-full rounded-xl border border-white/8 bg-black/[0.16] px-3 py-2.5 text-sm text-theme-text outline-none focus:border-theme-accent/30 disabled:cursor-default disabled:opacity-70"
            />
            {field?.secret ? (
              <button
                type="button"
                onClick={onToggleReveal}
                className="rounded-xl border border-white/10 bg-black/[0.16] p-2 text-theme-text-muted transition-colors hover:text-theme-text"
                aria-label={revealed ? 'Hide replacement value' : 'Reveal replacement value'}
              >
                {revealed ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            ) : null}
          </div>
        )}
      </div>

      {isReadOnly && field?.readOnlyReason ? (
        <p className="mt-2 text-[11px] text-theme-text-muted">{field.readOnlyReason}</p>
      ) : null}

      {field?.secret ? (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] text-theme-text-muted">
            {cleared ? 'The stored secret will be removed when you save.' : field?.hasValue ? 'Leave blank to keep the stored secret. Enter a new value to replace it.' : 'Enter a value to store this secret.'}
          </p>
          {field?.clearable && field?.hasValue ? (
            <ToolbarButton
              icon={cleared ? Undo2 : Trash2}
              label={cleared ? 'Keep stored secret' : 'Clear stored secret'}
              onClick={onClearSecret}
            />
          ) : null}
        </div>
      ) : field?.default !== undefined && field?.default !== null ? (
        <p className="mt-2 text-[11px] text-theme-text-muted">
          Default: <span className="font-mono text-theme-text">{String(field.default)}</span>
        </p>
      ) : null}
      {issues.map((issue, index) => (
        <p key={`${field?.key}-issue-${index}`} className="mt-1 text-[11px] text-yellow-100/90">{issue}</p>
      ))}
    </div>
  )
}

function Badge({ children, muted = false }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.14em] ${
      muted ? 'border-white/10 bg-black/[0.16] text-theme-text-muted/75' : 'border-theme-accent/20 bg-theme-accent/10 text-theme-text'
    }`}>{children}</span>
  )
}
