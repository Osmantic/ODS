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
  test('offers compact category navigation and search without behavior cards', () => {
    const onSearchChange = vi.fn(), onSectionChange = vi.fn()
    renderEditor({ onSearchChange, onSectionChange, sections: [...baseSections, { id: 'ports', title: 'Ports', keys: [] }] })
    fireEvent.click(screen.getByRole('combobox', { name: 'Configuration category' }))
    fireEvent.click(screen.getByRole('option', { name: 'Ports · 0' }))
    expect(onSectionChange).toHaveBeenCalledWith('ports')
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter configuration fields' }), { target: { value: 'model' } })
    expect(onSearchChange).toHaveBeenCalledWith('model')
    expect(screen.queryByText(/Save Behavior|Restart Behavior|Apply Behavior/)).toBeNull()
  })

  describe('managed host platform', () => {
    const MAC_BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Safari/605.1.15'
    const WINDOWS_BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'
    const field = (key, label, description, platforms = []) => ({
      key, label, type: 'string', description, required: false, secret: false, enum: [], default: null, platforms,
    })
    const platformFields = {
      LLAMA_ARG_CTX_CHECKPOINTS: field('LLAMA_ARG_CTX_CHECKPOINTS', 'Ctx Checkpoints', 'Prompt checkpoints per slot. Native macOS uses 32 when unset.'),
      LLAMA_SPEC_TYPE: field('LLAMA_SPEC_TYPE', 'Spec Type', 'Speculative decoding for the NVIDIA and CPU overlays and native macOS.'),
      ODS_AGENT_BIND: field('ODS_AGENT_BIND', 'Agent Bind', 'Defaults to 127.0.0.1 on macOS/Windows.'),
      HOST_RAM_GB: field('HOST_RAM_GB', 'Host RAM GB', 'Unified system RAM in GB.', ['macos']),
      ODS_MACOS_VM_IP: field('ODS_MACOS_VM_IP', 'Colima VM IP', 'Colima VM address.', ['macos']),
    }
    const runtime = { id: 'runtime', title: 'Runtime', keys: ['LLAMA_ARG_CTX_CHECKPOINTS', 'LLAMA_SPEC_TYPE', 'ODS_AGENT_BIND', 'HOST_RAM_GB'] }
    const bridge = { id: 'macos-bridge', title: 'macOS Bridge', keys: ['ODS_MACOS_VM_IP'] }
    const renderForHost = (hostPlatform, userAgent) => {
      vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue(userAgent)
      renderEditor({
        editor: { ...baseEditor, hostPlatform },
        fields: platformFields,
        values: Object.fromEntries(Object.keys(platformFields).map(key => [key, ''])),
        sections: [runtime, bridge],
        activeSection: bridge,
      })
      fireEvent.click(screen.getByRole('combobox', { name: 'Configuration category' }))
    }
    const fieldLabel = label => screen.queryByText(label, { selector: 'label' })

    afterEach(() => vi.restoreAllMocks())

    test('keeps a Mac server\'s macOS-only settings when managed from a Windows browser', () => {
      renderForHost('macos', WINDOWS_BROWSER)

      expect(screen.getByRole('option', { name: 'Runtime · 4' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: 'macOS Bridge · 1' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'macOS Bridge' })).toBeInTheDocument()
      expect(fieldLabel('Colima VM IP')).toBeInTheDocument()
    })

    test('hides macOS-only settings of a Linux server managed from a Mac browser', () => {
      renderForHost('other', MAC_BROWSER)

      expect(screen.getByRole('option', { name: 'Runtime · 3' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: /macOS Bridge/ })).toBeNull()
      // The active section disappeared, so the sidebar and the field pane both move to Runtime.
      expect(screen.getByRole('heading', { name: 'Runtime' })).toBeInTheDocument()
      expect(fieldLabel('Host RAM GB')).toBeNull()
      expect(fieldLabel('Colima VM IP')).toBeNull()
    })

    test('keeps shared fields whose descriptions mention macOS on non-macOS servers', () => {
      renderForHost('other', WINDOWS_BROWSER)

      expect(fieldLabel('Ctx Checkpoints')).toBeInTheDocument()
      expect(fieldLabel('Spec Type')).toBeInTheDocument()
      expect(fieldLabel('Agent Bind')).toBeInTheDocument()
    })

    test('shows every field when the server platform is unknown', () => {
      renderForHost(null, WINDOWS_BROWSER)

      expect(screen.getByRole('option', { name: 'Runtime · 4' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: 'macOS Bridge · 1' })).toBeInTheDocument()
      expect(fieldLabel('Colima VM IP')).toBeInTheDocument()
    })
  })

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

    expect(screen.getByRole('combobox', { name: /ODS Mode/i })).toBeDisabled()
    expect(screen.getByText('read only')).toBeInTheDocument()
    expect(screen.getByText(/selected by the installer/i)).toBeInTheDocument()
  })
})

test.each([
  ['1','True'],['yes','True'],[' ON ','True'],['TRUE','True'],
  ['0','False'],['no','False'],[' OFF ','False'],['FALSE','False'],
  [true,'True'],[false,'False'],['','Default'],
])('shows the settings API boolean value %j as %s without changing the draft', (value, selected) => {
  const onFieldChange = vi.fn()
  const section = {id:'webui',title:'WebUI',keys:['WEBUI_AUTH']}
  renderEditor({sections:[section],activeSection:section,
    fields:{WEBUI_AUTH:{key:'WEBUI_AUTH',label:'WebUI Auth',type:'boolean',default:true}},
    values:{WEBUI_AUTH:value},onFieldChange})
  expect(screen.getByRole('button',{name:selected,exact:true})).toHaveAttribute('aria-pressed','true')
  expect(onFieldChange).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'False',exact:true}))
  expect(onFieldChange).toHaveBeenCalledWith('WEBUI_AUTH','false')
})

test('does not silently present an invalid boolean as Default or False', () => {
  const section = {id:'webui',title:'WebUI',keys:['WEBUI_AUTH']}
  renderEditor({sections:[section],activeSection:section,
    fields:{WEBUI_AUTH:{key:'WEBUI_AUTH',label:'WebUI Auth',type:'boolean'}},
    values:{WEBUI_AUTH:'sometimes'},issues:[{key:'WEBUI_AUTH',message:'Must be true or false.'}],
    issueMap:{WEBUI_AUTH:['Must be true or false.']}})
  for (const name of ['Default','True','False']) expect(screen.getByRole('button',{name,exact:true})).toHaveAttribute('aria-pressed','false')
  expect(screen.getAllByText('Must be true or false.')).not.toHaveLength(0)
})

test.each([false, true])('shows an unsupported enum value even when readOnly=%s', readOnly => {
  const onFieldChange = vi.fn()
  const section = {id:'llm',title:'LLM',keys:['ODS_MODE']}
  renderEditor({sections:[section], activeSection:section,
    fields:{ODS_MODE:{key:'ODS_MODE',label:'ODS Mode',type:'string',enum:['local','cloud'],default:'local',readOnly}},
    values:{ODS_MODE:'legacy-mode'}, onFieldChange,
    issues:[{key:'ODS_MODE',message:'Must be one of local, cloud.'}],
    issueMap:{ODS_MODE:['Must be one of local, cloud.']}})
  const input = screen.getByRole('combobox', {name:'ODS Mode'})
  expect(input).toHaveValue('legacy-mode')
  expect(input.selectedOptions[0]).toHaveTextContent('Unsupported value: legacy-mode')
  expect(onFieldChange).not.toHaveBeenCalled()
  expect(input.disabled).toBe(readOnly)
  if (!readOnly) {
    fireEvent.change(input, {target:{value:'cloud'}})
    expect(onFieldChange).toHaveBeenCalledWith('ODS_MODE','cloud')
    fireEvent.change(input, {target:{value:''}})
    expect(onFieldChange).toHaveBeenLastCalledWith('ODS_MODE','')
  }
})

test.each(['local', ''])('keeps the existing enum selection %j unchanged', value => {
  const section = {id:'llm',title:'LLM',keys:['ODS_MODE']}
  renderEditor({sections:[section], activeSection:section,
    fields:{ODS_MODE:{key:'ODS_MODE',label:'ODS Mode',type:'string',enum:['local','cloud'],default:'local'}},
    values:{ODS_MODE:value}})
  const input = screen.getByRole('combobox', {name:'ODS Mode'})
  expect(input).toHaveValue(value)
  expect(input.querySelectorAll('option')).toHaveLength(3)
})
