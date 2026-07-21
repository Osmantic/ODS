import { fireEvent, screen } from '@testing-library/react'
import { render } from '../../test/test-utils'
import EnvEditor from '../settings/EnvEditor' // eslint-disable-line no-unused-vars

const baseEditor = {
  path: '.env',
  saveHint: 'Saving keeps existing secret values when left blank.',
  restartHint: 'Restart to apply service-level changes.',
  backupPath: null,
}

const baseFields = {
  OPENAI_API_KEY: {
    key: 'OPENAI_API_KEY',
    label: 'OpenAI API Key',
    type: 'string',
    description: 'Cloud provider API key.',
    required: false,
    secret: true,
    hasValue: true,
    enum: [],
    default: null,
  },
}

const baseSections = [
  {
    id: 'llm-settings',
    title: 'LLM Settings',
    keys: ['OPENAI_API_KEY'],
  },
]

const renderEditor = (overrides = {}) =>
  render(
    <EnvEditor
      editor={baseEditor}
      search=""
      onSearchChange={() => {}}
      sections={baseSections}
      activeSection={baseSections[0]}
      onSectionChange={() => {}}
      fields={baseFields}
      values={{ OPENAI_API_KEY: '' }}
      issues={[]}
      issueMap={{}}
      revealedSecrets={{}}
      onToggleReveal={() => {}}
      onFieldChange={() => {}}
      onReload={() => {}}
      onSave={() => {}}
      dirty={false}
      saving={false}
      {...overrides}
    />
  )

describe('EnvEditor', () => {
  test('renders stored secrets as masked placeholders instead of exposing values', () => {
    renderEditor()

    expect(screen.getByRole('textbox', { name: /filter configuration fields/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Stored locally')).toBeInTheDocument()
    expect(screen.getByText(/Leave blank to keep the stored secret/i)).toBeInTheDocument()
    expect(screen.queryByDisplayValue('sk-live-secret')).not.toBeInTheDocument()
  })

  test('shows when a secret is not configured yet', () => {
    renderEditor({
      fields: {
        OPENAI_API_KEY: {
          ...baseFields.OPENAI_API_KEY,
          hasValue: false,
        },
      },
    })

    expect(screen.getByPlaceholderText('Not set')).toBeInTheDocument()
    expect(screen.getByText(/Enter a value to store this secret/i)).toBeInTheDocument()
  })

  test('requires an explicit action to clear a clearable stored secret', () => {
    let clearRequested = false
    renderEditor({
      fields: {
        RAG_OPENAI_API_KEY: {
          ...baseFields.OPENAI_API_KEY,
          key: 'RAG_OPENAI_API_KEY',
          label: 'RAG OpenAI API Key',
          clearable: true,
        },
      },
      values: { RAG_OPENAI_API_KEY: '' },
      sections: [{ id: 'rag', title: 'RAG', keys: ['RAG_OPENAI_API_KEY'] }],
      activeSection: { id: 'rag', title: 'RAG', keys: ['RAG_OPENAI_API_KEY'] },
      onClearSecret: () => { clearRequested = true },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Clear stored secret' }))
    expect(clearRequested).toBe(true)
  })

  test('shows pending secret removal and lets the operator undo it', () => {
    renderEditor({
      fields: {
        RAG_OPENAI_API_KEY: {
          ...baseFields.OPENAI_API_KEY,
          key: 'RAG_OPENAI_API_KEY',
          label: 'RAG OpenAI API Key',
          clearable: true,
        },
      },
      values: { RAG_OPENAI_API_KEY: '' },
      sections: [{ id: 'rag', title: 'RAG', keys: ['RAG_OPENAI_API_KEY'] }],
      activeSection: { id: 'rag', title: 'RAG', keys: ['RAG_OPENAI_API_KEY'] },
      clearedSecrets: ['RAG_OPENAI_API_KEY'],
    })

    expect(screen.getByText(/will be removed when you save/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Keep stored secret' })).toBeEnabled()
  })

  test('enables apply button when a saved runtime change can be applied', () => {
    renderEditor({
      applyPlan: {
        status: 'ready',
        supported: true,
        services: ['llama-server'],
        summary: 'Saved changes are ready to apply to llama-server.',
      },
    })

    expect(screen.getByRole('button', { name: /apply changes/i })).toBeEnabled()
    expect(screen.getByText(/Pending runtime changes/i)).toBeInTheDocument()
    expect(screen.getAllByText(/llama-server/i).length).toBeGreaterThan(0)
  })

  test('renders required RAG reindex action after an embedding model change', () => {
    renderEditor({
      applyPlan: {
        status: 'ready',
        supported: true,
        services: ['embeddings', 'open-webui'],
        summary: 'Saved changes are ready to apply to embeddings, open-webui.',
        postApplyActions: [{
          id: 'open-webui-rag-reindex',
          title: 'Reindex Open WebUI knowledge bases',
          message: 'Confirm the embedding model and run Reindex.',
        }],
      },
    })

    expect(screen.getByText('Reindex Open WebUI knowledge bases')).toBeInTheDocument()
    expect(screen.getByText(/Confirm the embedding model and run Reindex/i)).toBeInTheDocument()
  })

  test('keeps RAG actions visible as required follow-up after apply', () => {
    renderEditor({
      followUpPlan: {
        status: 'post-apply',
        summary: 'Runtime changes were applied. Complete the required follow-up below.',
        postApplyActions: [{
          id: 'open-webui-rag-sync',
          title: 'Apply RAG settings in Open WebUI',
          message: 'Set the saved values in the Open WebUI Admin Panel.',
        }],
      },
    })

    expect(screen.getByText('Required follow-up')).toBeInTheDocument()
    expect(screen.getByText('Apply RAG settings in Open WebUI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark complete' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
  })

  test('renders runtime-managed fields as read only', () => {
    const modeField = {
      key: 'ODS_MODE',
      label: 'ODS Mode',
      type: 'string',
      description: 'LLM backend mode.',
      required: false,
      secret: false,
      enum: ['local', 'cloud'],
      default: 'local',
      readOnly: true,
      readOnlyReason: 'Runtime mode is selected by the installer and cannot be changed from the dashboard.',
    }
    const modeSection = { id: 'llm-settings', title: 'LLM Settings', keys: ['ODS_MODE'] }

    renderEditor({
      sections: [modeSection],
      activeSection: modeSection,
      fields: { ODS_MODE: modeField },
      values: { ODS_MODE: 'local' },
    })

    expect(screen.getByRole('combobox')).toBeDisabled()
    expect(screen.getByText('read only')).toBeInTheDocument()
    expect(screen.getByText(/selected by the installer/i)).toBeInTheDocument()
  })
})
