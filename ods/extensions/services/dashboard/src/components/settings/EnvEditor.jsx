import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Database,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Folder,
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

const sectionDescription = (section) => {
  const title = String(section?.title || '').toLowerCase()
  if (title.includes('required')) return 'Required values read from the active .env schema.'
  if (title.includes('network') || title.includes('port')) return 'Network bindings and port overrides read from the active .env schema.'
  if (title.includes('llm') || title.includes('cloud')) return 'Model backend, hosted LLM, and provider settings from the active .env schema.'
  if (title.includes('langfuse')) return 'LLM observability settings from the active .env schema.'
  if (title.includes('gpu')) return 'GPU and runtime allocation settings from the active .env schema.'
  if (title.includes('security')) return 'Security-related environment values from the active .env schema.'
  if (title.includes('proxy')) return 'LAN proxy settings from the active .env schema.'
  return 'Fields and descriptions are generated from .env.schema.json and the current .env file.'
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
    <section className="settings-premium-card liquid-metal-frame liquid-metal-frame--soft rounded-lg border border-theme-border p-5 lg:p-7">
      <EnvironmentEditorHeader
        onRefresh={onRefresh || onReload}
        onReload={onReload}
        onSave={onSave}
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

        <EnvironmentBehaviorCards
          editor={editor}
          canApply={canApply}
          applyPlan={applyPlan}
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

        <div className="grid gap-5 xl:grid-cols-[320px_1fr]">
          <EnvironmentCategorySidebar
            search={search}
            onSearchChange={onSearchChange}
            sections={sections}
            activeSection={activeSection}
            onSectionChange={onSectionChange}
          />

          <div className="rounded-lg border border-theme-border bg-theme-card p-5">
            {activeSection ? (
              <>
                <div className="mb-5 flex flex-col gap-4 border-b border-theme-border pb-5 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex items-start gap-3">
                    <SlidersHorizontal size={20} className="mt-1 shrink-0 text-theme-accent-light" />
                    <div>
                      <h3 className="text-xl font-semibold text-theme-text">{activeSection.title}</h3>
                      <p className="mt-1 text-sm leading-6 text-theme-text-muted">{sectionDescription(activeSection)}</p>
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

                <div className="max-h-[58rem] space-y-4 overflow-y-auto pr-1">
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
                  <EnvironmentHelpCard />
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

function EnvironmentEditorHeader({ onRefresh, onReload, onSave, onApply, saving, applying, dirty, canApply }) {
  return (
    <header className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
      <div className="flex items-start gap-3.5">
        <Database size={22} strokeWidth={1.7} className="mt-1 shrink-0 text-theme-accent-light" />
        <div>
          <h2 className="text-2xl font-semibold text-theme-text">Environment Editor</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-theme-text-muted">
            Edit the ODS .env file directly from the dashboard. Changes are validated and applied securely.
          </p>
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
      </div>
    </header>
  )
}

function EnvironmentStatusStrip({ editor, fieldCount, issueCount, issueSectionCount }) {
  return (
    <div className="grid gap-4 rounded-lg border border-theme-border bg-theme-bg/30 px-5 py-4 md:grid-cols-[1.2fr_1fr_1fr_1fr] md:items-center">
      <div className="flex items-center gap-3">
        <Folder size={19} className="shrink-0 text-theme-accent-light" />
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted">Local Configuration</p>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <p className="text-sm font-semibold text-theme-text">Editing ODS .env</p>
            <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${editor?.agentAvailable === false ? 'text-yellow-200' : 'text-emerald-300'}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${editor?.agentAvailable === false ? 'bg-yellow-300' : 'bg-emerald-400'}`} />
              {editor?.agentAvailable === false ? 'Agent offline' : 'Connected'}
            </span>
          </div>
        </div>
      </div>
      <StatusMetric icon={FileText} value={fieldCount} label="Fields" />
      <StatusMetric icon={AlertTriangle} value={issueCount} label="Validation Issues" />
      <StatusMetric icon={Folder} value={issueSectionCount} label="Sections with Issues" />
    </div>
  )
}

function EnvironmentBehaviorCards({ editor, canApply, applyPlan }) {
  const applyText = editor?.agentAvailable === false
    ? 'ODS host agent is offline. Start it first, then use Apply Changes to recreate affected services.'
    : canApply
      ? `Apply changes will recreate: ${applyPlan.services.join(', ')}.`
      : 'Apply Changes becomes available after saving keys that affect running services. You will see affected services before applying.'

  return (
    <div className="grid overflow-hidden rounded-lg border border-theme-border bg-theme-bg/30 lg:grid-cols-3 lg:divide-x lg:divide-theme-border">
      <BehaviorCard icon={Download} title="Save Behavior" text={editor.saveHint || 'Saving writes the .env file directly, preserves blank secrets, and stores a backup first.'} />
      <BehaviorCard icon={RefreshCw} title="Restart Behavior" text={editor.restartHint || 'Some ODS services need a container recreate before changes take effect.'} />
      <BehaviorCard icon={Zap} title="Apply Behavior" text={applyText} />
    </div>
  )
}

function EnvironmentCategorySidebar({ search, onSearchChange, sections, activeSection, onSectionChange }) {
  const grouped = groupSections(sections)
  return (
    <aside className="self-start rounded-lg border border-theme-border bg-theme-card p-3 xl:sticky xl:top-6">
      <label className="flex items-center gap-2 rounded-md border border-theme-border bg-theme-bg/35 px-3 py-2.5">
        <span className="sr-only">Filter configuration fields</span>
        <Search size={15} className="text-theme-text-muted" />
        <input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Filter categories..."
          aria-label="Filter configuration fields"
          className="min-w-0 flex-1 bg-transparent text-sm text-theme-text outline-none placeholder:text-theme-text-muted/55"
        />
      </label>

      <div className="mt-3 max-h-[60rem] overflow-y-auto pr-1">
        {grouped.map(group => (
          <div key={group.id} className="mb-4 last:mb-0">
            <div className="mb-2 flex items-center gap-2 px-2">
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-theme-text-muted">{group.title}</p>
              <span className="h-px flex-1 bg-theme-border" />
            </div>
            <div className="space-y-1">
              {group.sections.map(section => (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => onSectionChange(section.id)}
                  aria-pressed={activeSection?.id === section.id}
                  className={`group relative flex w-full items-center justify-between gap-3 rounded-md px-3 py-2.5 text-left transition-colors ${
                    activeSection?.id === section.id
                      ? 'bg-theme-accent/24 text-theme-text shadow-[inset_3px_0_0_rgba(215,164,255,0.95)]'
                      : 'text-theme-text-muted hover:bg-theme-surface-hover hover:text-theme-text'
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{section.title}</span>
                  </span>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                    activeSection?.id === section.id ? 'bg-theme-accent/35 text-theme-accent-light' : 'text-theme-text-muted/65'
                  }`}>
                    {section.keys.length}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

function EnvironmentFieldCard({ field, value, issues, revealed, cleared, onToggleReveal, onClearSecret, onChange }) {
  const hasIssues = issues.length > 0
  const isEnum = Array.isArray(field?.enum) && field.enum.length > 0
  const isBoolean = field?.type === 'boolean'
  const isInteger = field?.type === 'integer'
  const isReadOnly = Boolean(field?.readOnly)
  const secretPlaceholder = field?.secret ? (field?.hasValue ? 'Stored locally' : 'Not set') : (field?.default !== undefined && field?.default !== null ? String(field.default) : '')
  const looksValid = !hasIssues && value !== ''
  const versionLike = /version/i.test(field?.key || '')

  return (
    <div className={`rounded-lg border px-5 py-5 ${hasIssues ? 'border-yellow-500/25 bg-yellow-500/5' : 'border-theme-border bg-theme-bg/30'}`}>
      <div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-base font-semibold text-theme-text">{field?.label}</p>
            <Badge muted>{fieldKeyLabel(field?.key)}</Badge>
            {field?.secret ? <Badge accent>Secret</Badge> : null}
            {field?.required ? <Badge>Required</Badge> : <Badge muted>Optional</Badge>}
            {isReadOnly ? <Badge muted>read only</Badge> : null}
          </div>
          <p className="mt-2 text-sm leading-6 text-theme-text-muted">{field?.description || 'No description available.'}</p>
        </div>
      </div>

      <div className="mt-4">
        {isBoolean ? (
          <div className="inline-flex rounded-lg border border-theme-border bg-theme-bg/40 p-1">
            {[
              { id: '', label: 'Default' },
              { id: 'true', label: 'True' },
              { id: 'false', label: 'False' },
            ].map((option) => (
              <button
                key={option.label}
                type="button"
                disabled={isReadOnly}
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
      ) : looksValid ? (
        <p className="mt-3 flex items-center gap-2 text-xs text-emerald-300">
          <CheckCircle2 size={14} />
          {versionLike ? 'Valid version format' : 'Value looks valid'}
        </p>
      ) : field?.default !== undefined && field?.default !== null ? (
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

function EnvironmentHelpCard() {
  return (
    <div className="flex flex-col gap-4 border-t border-theme-border px-1 pt-5 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-start gap-3">
        <BookOpen size={18} className="mt-0.5 shrink-0 text-theme-text-muted" />
        <div>
          <p className="text-sm font-semibold text-theme-text">Environment reference</p>
          <p className="mt-1 text-sm text-theme-text-muted">Field labels, helper text, defaults, and validation come from the active ODS environment schema.</p>
        </div>
      </div>
      <a href="https://github.com/Osmantic/ODS/tree/main/ods/docs" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 text-sm font-medium text-theme-accent-light hover:text-theme-text">
        Open documentation
        <ExternalLink size={15} />
      </a>
    </div>
  )
}

function StatusMetric({ icon: Icon, value, label }) {
  return (
    <div className="flex items-center gap-3 border-theme-border md:justify-center md:border-l">
      <Icon size={18} className="text-theme-text-muted" strokeWidth={1.6} />
      <div>
        <p className="text-lg font-semibold text-theme-text">{value}</p>
        <p className="text-xs text-theme-text-muted">{label}</p>
      </div>
    </div>
  )
}

function BehaviorCard({ icon: Icon, title, text }) {
  return (
    <div className="p-4">
      <div className="flex items-start gap-3">
        <Icon size={17} className="mt-0.5 shrink-0 text-theme-accent-light" />
        <div>
          <p className="text-sm font-semibold text-theme-text">{title}</p>
          <p className="mt-1 text-xs leading-5 text-theme-text-muted">{text}</p>
        </div>
      </div>
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
