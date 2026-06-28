import { screen } from '@testing-library/react'
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
})
