import {
  clearSettingsFollowUp,
  loadSettingsFollowUp,
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
