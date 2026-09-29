import {
  clearSettingsApplyPlan,
  clearSettingsFollowUp,
  loadSettingsApplyPlan,
  loadSettingsFollowUp,
  mergeSettingsApplyPlans,
  saveSettingsApplyPlan,
  saveSettingsFollowUp,
  settleSettingsApplyPlan,
} from './settingsApplyPlan'

const followUpAction = {
  id: 'open-webui-rag-reindex',
  title: 'Reindex Open WebUI knowledge bases',
  message: 'Reindex after changing the embedding model.',
}

describe('settings apply-plan state', () => {
  beforeEach(() => globalThis.localStorage.clear())

  test('persists pending runtime work across a dashboard reload and clears it when settled', () => {
    const plan = {
      status: 'ready',
      supported: true,
      services: ['open-webui'],
      changedKeys: ['RAG_OPENAI_API_KEY'],
      manualKeys: [],
      inactiveServices: [],
      summary: 'Saved changes are ready to apply to open-webui.',
      postApplyActions: [{
        id: 'open-webui-rag-sync',
        title: 'Apply RAG settings in Open WebUI',
        message: 'Update the Open WebUI settings after recreation.',
      }],
    }

    saveSettingsApplyPlan(plan)
    expect(loadSettingsApplyPlan()).toEqual(plan)

    const merged = mergeSettingsApplyPlans(loadSettingsApplyPlan(), {
      status: 'ready',
      services: ['llama-server'],
      changedKeys: ['CTX_SIZE'],
      manualKeys: [],
      inactiveServices: [],
      summary: 'Saved changes are ready to apply to llama-server.',
      postApplyActions: [],
    })
    expect(merged.services).toEqual(['llama-server', 'open-webui'])
    expect(merged.changedKeys).toEqual(['CTX_SIZE', 'RAG_OPENAI_API_KEY'])

    clearSettingsApplyPlan()
    expect(loadSettingsApplyPlan()).toBeNull()
  })

  test('rejects malformed stored apply plans and tolerates blocked browser storage', () => {
    globalThis.localStorage.setItem('ods-settings-apply-plan-v1', JSON.stringify({ services: [42] }))
    expect(loadSettingsApplyPlan()).toBeNull()

    const blockedStorage = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
      removeItem: () => { throw new Error('blocked') },
    }
    expect(loadSettingsApplyPlan(blockedStorage)).toBeNull()
    expect(saveSettingsApplyPlan({ services: ['open-webui'] }, blockedStorage).services).toEqual(['open-webui'])
    expect(() => clearSettingsApplyPlan(blockedStorage)).not.toThrow()
  })

  test('persists a validated follow-up across a page reload', () => {
    saveSettingsFollowUp({ postApplyActions: [followUpAction] })

    expect(loadSettingsFollowUp()).toEqual({
      status: 'post-apply',
      summary: 'Runtime changes were applied. Complete the required follow-up below.',
      postApplyActions: [followUpAction],
    })
  })

  test('rejects malformed stored follow-up content', () => {
    globalThis.localStorage.setItem('ods-settings-follow-up-v1', JSON.stringify({
      postApplyActions: [{ id: 'missing-fields' }],
    }))

    expect(loadSettingsFollowUp()).toBeNull()
  })

  test('clears a completed follow-up receipt', () => {
    saveSettingsFollowUp({ postApplyActions: [followUpAction] })
    clearSettingsFollowUp()

    expect(loadSettingsFollowUp()).toBeNull()
  })

  test('does not crash when browser storage is unavailable', () => {
    const blockedStorage = {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
      removeItem: () => { throw new Error('blocked') },
    }

    expect(loadSettingsFollowUp(blockedStorage)).toBeNull()
    expect(saveSettingsFollowUp({ postApplyActions: [followUpAction] }, blockedStorage))
      .toMatchObject({ postApplyActions: [followUpAction] })
    expect(() => clearSettingsFollowUp(blockedStorage)).not.toThrow()
  })

  test('retains manual restart work after runtime services are applied', () => {
    const result = settleSettingsApplyPlan({
      status: 'partial',
      services: ['embeddings', 'open-webui'],
      manualKeys: ['BIND_ADDRESS'],
      inactiveServices: ['qdrant'],
      postApplyActions: [followUpAction],
    })

    expect(result.remainingPlan).toMatchObject({
      status: 'manual',
      supported: false,
      services: [],
      manualKeys: ['BIND_ADDRESS'],
      inactiveServices: ['qdrant'],
    })
    expect(result.remainingPlan.summary).toContain('Configuration remains staged')
    expect(result.followUpPlan.postApplyActions).toEqual([followUpAction])
  })
})
