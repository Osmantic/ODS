import PanelSelect from '../PanelSelect'
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Download,
  Eye,
  EyeOff,
  Lock,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  SlidersHorizontal,
  Trash2,
  Undo2,
  Zap,
} from 'lucide-react'

const GROUPS = [
  { id: 'core', title: 'Core Settings', match: ['configuration', 'required', 'network', 'llm', 'cloud'] },
  { id: 'infrastructure', title: 'Infrastructure', match: ['ports', 'security', 'langfuse', 'multi-gpu', 'voice', 'web', 'tools', 'agent policy'] },
  { id: 'advanced', title: 'Advanced', match: [] },
]

const fieldKeyLabel = (key = '') => key.toLowerCase()

const countIssueSections = (sections, issues) => {
  const issueKeys = new Set((issues || []).map(issue => issue.key).filter(Boolean))
  return (sections || []).filter(section => section.keys?.some(key => issueKeys.has(key))).length
}

const groupSections = (sections = []) => {
  const grouped = GROUPS.map(group => ({ ...group, sections: [] }))
  for (const section of sections) {
    const label = `${section.id} ${section.title}`.toLowerCase()
    const match = grouped.find(group => group.match.some(token => label.includes(token)))
    ;(match || grouped[grouped.length - 1]).sections.push(section)
  }
  return grouped.filter(group => group.sections.length > 0)
}

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
  onRefresh,
  onReload,
  onSave,
  onExport,
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
  const issueSectionCount = countIssueSections(sections, issues)

  return (
    <section className="settings-premium-card settings-environment p-5 lg:p-7">
      <EnvironmentEditorHeader
        onRefresh={onRefresh || onReload}
        onReload={onReload}
        onSave={onSave}
        onExport={onExport}
        onApply={onApply}
        saving={saving}
        applying={applying}
        dirty={dirty}
        canApply={canApply}
      />

      <div className="mt-6 space-y-5">
        <EnvironmentStatusStrip
          editor={editor}
          fieldCount={Object.keys(fields || {}).length}
          issueCount={issues.length}
          issueSectionCount={issueSectionCount}
        />

        {applyPlan?.status && applyPlan.status !== 'none' ? (
          <div className="rounded-lg border border-theme-accent/25 bg-theme-accent/10 px-5 py-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-accent-light">
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
          <div className="rounded-lg border border-theme-accent/25 bg-theme-accent/10 px-5 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-accent-light">Required follow-up</p>
                <p className="mt-1 text-sm text-theme-text">{followUpPlan.summary}</p>
              </div>
              <ToolbarButton icon={CheckCircle2} label="Mark complete" onClick={onCompleteFollowUp} />
            </div>
            {followUpPlan.postApplyActions?.map((action) => (
              <div key={action.id} className="mt-3 border-t border-theme-accent/15 pt-3">
                <p className="text-xs font-semibold text-theme-text">{action.title}</p>
                <p className="mt-1 text-xs text-theme-text-muted">{action.message}</p>
              </div>
            ))}
          </div>
        ) : null}

        {issues.length > 0 ? (
          <div className="rounded-lg border border-yellow-500/25 bg-yellow-500/10 px-5 py-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-yellow-100">Validation notes</p>
            <div className="mt-2 space-y-1">
              {issues.slice(0, 8).map((issue, index) => (
                <p key={`${issue.key || 'line'}-${index}`} className="text-sm text-yellow-50/90">
                  {issue.key ? `${issue.key}: ` : ''}{issue.message}
                </p>
              ))}
            </div>
          </div>
        ) : null}

        <div className="environment-editor-layout">
          <EnvironmentCategorySidebar
            search={search}
            onSearchChange={onSearchChange}
            sections={sections}
            activeSection={activeSection}
            onSectionChange={onSectionChange}
          />

          <div className="settings-environment-fields">
            {activeSection ? (
              <>
                <div className="mb-5 flex flex-col gap-4 border-b border-theme-border pb-5 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex items-start gap-3">
                    <SlidersHorizontal size={20} className="mt-1 shrink-0 text-theme-accent-light" />
                    <div>
                      <h3 className="text-xl font-semibold text-theme-text">{activeSection.title}</h3>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-4 text-xs">
                    <span className="text-theme-text-muted">{activeKeys.length} fields</span>
                    <span className={`inline-flex items-center gap-1.5 ${activeKeys.some(key => issueMap[key]?.length) ? 'text-amber-300' : 'text-emerald-300'}`}>
                      {activeKeys.some(key => issueMap[key]?.length) ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
                      {activeKeys.some(key => issueMap[key]?.length) ? 'Needs review' : 'Validated'}
                    </span>
                  </div>
                </div>

                <div className="environment-field-list">
                  {activeKeys.map((key) => (
                    <EnvironmentFieldCard
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
              </>
            ) : (
              <div className="rounded-lg border border-theme-border bg-theme-bg/30 px-5 py-8 text-sm text-theme-text-muted">
                No fields match the current filter.
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

function EnvironmentEditorHeader({ onRefresh, onReload, onSave, onExport, onApply, saving, applying, dirty, canApply }) {
  return (
    <header className="settings-environment-header flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
      <div className="flex items-start gap-3.5">
        <Database size={22} strokeWidth={1.7} className="mt-1 shrink-0 text-theme-accent-light" />
        <div>
          <h2 className="text-2xl font-semibold text-theme-text">Environment Editor</h2>
          <p className="mt-1 text-sm text-theme-text-muted">{dirty ? 'Unsaved changes' : 'Local .env configuration'}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 lg:justify-end">
        <button onClick={onRefresh} className="flex items-center gap-2 text-sm font-medium text-theme-accent-light hover:text-theme-text">
          <RefreshCw size={16} />
          Refresh
        </button>
        <ToolbarButton icon={RotateCcw} label="Reload" onClick={onReload} />
        <ToolbarButton icon={Save} label={saving ? 'Saving...' : 'Save .env'} onClick={onSave} disabled={!dirty || saving} />
        <ToolbarButton icon={Zap} label={applying ? 'Applying...' : 'Apply changes'} onClick={onApply} primary disabled={!canApply || applying || saving} />
        {onExport && <button type="button" onClick={onExport} aria-label="Export configuration" title="Export configuration" className="environment-export"><Download size={16} /></button>}
      </div>
    </header>
  )
}

function EnvironmentStatusStrip({ editor, fieldCount, issueCount, issueSectionCount }) {
  return <div className="environment-status-line">
    <span>{fieldCount} fields</span>
    <span>{issueCount ? `${issueCount} issues in ${issueSectionCount} sections` : 'No validation issues'}</span>
    {editor?.agentAvailable === false && <span className="text-amber-300">Host agent offline · applying unavailable</span>}
  </div>
}

function EnvironmentCategorySidebar({ search, onSearchChange, sections, activeSection, onSectionChange }) {
  const grouped = groupSections(sections)
  return (
    <div className="environment-navigation">
      <label className="flex items-center gap-2 rounded-md border border-theme-border bg-theme-bg/35 px-3 py-2.5">
        <span className="sr-only">Filter configuration fields</span>
        <Search size={15} className="text-theme-text-muted" />
        <input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search fields or categories…"
          aria-label="Filter configuration fields"
          className="min-w-0 flex-1 bg-transparent text-sm text-theme-text outline-none placeholder:text-theme-text-muted/55"
        />
      </label>

      <div className="environment-category-picker"><PanelSelect label="Configuration category" value={activeSection?.id || ''} onChange={onSectionChange}
        options={grouped.flatMap(group => group.sections.map(section => ({ value: section.id, label: `${section.title} · ${section.keys.length}`, group: group.title })))} /></div>
    </div>
  )
}

function EnvironmentFieldCard({ field, value, issues, revealed, cleared, onToggleReveal, onClearSecret, onChange }) {
  const hasIssues = issues.length > 0
  const isEnum = Array.isArray(field?.enum) && field.enum.length > 0
  const isBoolean = field?.type === 'boolean'
  const isInteger = field?.type === 'integer'
  const isReadOnly = Boolean(field?.readOnly)
  const secretPlaceholder = field?.secret ? (field?.hasValue ? 'Stored locally' : 'Not set') : (field?.default !== undefined && field?.default !== null ? String(field.default) : '')

  return (
    <div className={`settings-environment-field ${hasIssues ? 'settings-environment-field--issue' : ''}`}>
      <div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`env-field-${field?.key}`} className="text-base font-semibold text-theme-text">{field?.label}</label>
            <Badge muted>{fieldKeyLabel(field?.key)}</Badge>
            {field?.secret ? <Badge accent>Secret</Badge> : null}
            {field?.required && <Badge>Required</Badge>}
            {isReadOnly ? <Badge muted>read only</Badge> : null}
          </div>
          {field?.description && <p className="mt-2 text-sm leading-6 text-theme-text-muted">{field.description}</p>}
        </div>
      </div>

      <div className="mt-4">
        {isBoolean ? (
          <div id={`env-field-${field?.key}`} role="group" aria-label={field?.label} className="inline-flex rounded-lg border border-theme-border bg-theme-bg/40 p-1">
            {[
              { id: '', label: 'Default' },
              { id: 'true', label: 'True' },
              { id: 'false', label: 'False' },
            ].map((option) => (
              <button
                key={option.label}
                type="button"
                disabled={isReadOnly}
                aria-pressed={String(value).toLowerCase() === option.id}
                onClick={() => onChange(option.id)}
                className={`rounded-lg px-4 py-2 text-xs font-semibold uppercase tracking-[0.14em] transition-colors disabled:cursor-default disabled:opacity-60 ${
                  String(value).toLowerCase() === option.id ? 'bg-theme-accent text-white' : 'text-theme-text-muted hover:text-theme-text'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : isEnum ? (
          <select
            id={`env-field-${field?.key}`}
            value={value}
            disabled={isReadOnly}
            onChange={(event) => onChange(event.target.value)}
            className="w-full rounded-lg border border-theme-border bg-theme-bg/40 px-4 py-3 text-sm text-theme-text outline-none focus:border-theme-accent/60 disabled:cursor-default disabled:opacity-70"
          >
            <option value="">Use default</option>
            {field.enum.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        ) : (
          <div className="flex items-center gap-2">
            <input
              id={`env-field-${field?.key}`}
              type={field?.secret && !revealed ? 'password' : (isInteger ? 'number' : 'text')}
              value={value}
              disabled={isReadOnly}
              onChange={(event) => onChange(event.target.value)}
              placeholder={secretPlaceholder}
              autoComplete="off"
              className="w-full rounded-lg border border-theme-border bg-theme-bg/40 px-4 py-3 text-sm text-theme-text outline-none placeholder:text-theme-text-muted/55 focus:border-theme-accent/60 disabled:cursor-default disabled:opacity-70"
            />
            {field?.secret ? (
              <button
                type="button"
                onClick={onToggleReveal}
                className="rounded-lg border border-theme-border bg-theme-bg/40 p-3 text-theme-text-muted transition-colors hover:text-theme-text"
                aria-label={revealed ? 'Hide replacement value' : 'Reveal replacement value'}
              >
                {revealed ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            ) : null}
          </div>
        )}
      </div>

      {isReadOnly && field?.readOnlyReason ? (
        <p className="mt-3 text-xs text-theme-text-muted">{field.readOnlyReason}</p>
      ) : null}

      {field?.secret ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="flex items-center gap-2 text-xs text-theme-text-muted">
            <Lock size={13} className="text-yellow-300" />
            {cleared
              ? 'The stored secret will be removed when you save.'
              : field?.hasValue
                ? 'Leave blank to keep the stored secret. Enter a new value to replace it.'
                : 'Enter a value to store this secret.'}
          </p>
          {field?.clearable && field?.hasValue ? (
            <ToolbarButton
              icon={cleared ? Undo2 : Trash2}
              label={cleared ? 'Keep stored secret' : 'Clear stored secret'}
              onClick={onClearSecret}
            />
          ) : null}
        </div>
      ) : value === '' && field?.default !== undefined && field?.default !== null ? (
        <p className="mt-3 text-xs text-theme-text-muted">
          Default: <span className="font-mono text-theme-text">{String(field.default)}</span>
        </p>
      ) : null}

      {issues.map((issue, index) => (
        <p key={`${field?.key}-issue-${index}`} className="mt-2 flex items-center gap-2 text-xs text-yellow-100/90">
          <AlertTriangle size={13} />
          {issue}
        </p>
      ))}
    </div>
  )
}

function ToolbarButton({ icon: Icon, label, onClick, primary = false, disabled = false }) {
  const cls = primary
    ? 'liquid-metal-button border-theme-accent text-white disabled:cursor-default disabled:opacity-50'
    : 'border-theme-border bg-theme-card text-theme-text hover:border-theme-accent/45 disabled:cursor-default disabled:opacity-45'
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-lg border px-4 py-3 text-sm font-semibold ${cls}`}
    >
      <span className="flex items-center gap-2"><Icon size={16} />{label}</span>
    </button>
  )
}

function Badge({ children, muted = false, accent = false }) {
  const cls = accent
    ? 'border-theme-accent/20 bg-theme-accent/16 text-theme-accent-light'
    : muted
      ? 'border-theme-border bg-theme-bg/30 text-theme-text-muted'
      : 'border-theme-accent/25 bg-theme-accent/12 text-theme-accent-light'
  return (
    <span className={`rounded-lg border px-2 py-1 text-xs font-medium ${cls}`}>{children}</span>
  )
}
