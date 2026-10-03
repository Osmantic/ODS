import { useCallback, useEffect, useRef, useState } from 'react'
import { readIntegrationRecovery, saveIntegrationRecovery, readyIntegrationPlan, integrationRequestId } from '../lib/extensionIntegrationRecovery'

export function integrationRequest(installation, command, project, chatId) {
  if (typeof command !== 'string' || installation?.state !== 'succeeded' || installation.command !== command ||
      !chatId || installation.chatId !== chatId || !installation.requestId ||
      !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(installation.target || '') ||
      !/^Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(project || '')) return null
  const named = command.match(/Playground\/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/g) || []
  if (named.some(path => path !== project)) return null
  // Continue with actual model/tool work, not a synthetic assistant result or
  // another slash installation. The original request remains in chat history.
  return `Continue the preceding request by integrating @${installation.target} into ${project}. ` +
    'The extension is ready; project integration is still pending. ' +
    'Use its current integration guidance and the actual project files to make the requested changes. ' +
    'Preserve existing work, keep credentials private, and verify what the original request allows. ' +
    'If this application needs no code connection, explain how to use it with the project instead of creating placeholder files.'
}

export default function useExtensionProjectIntegration({ chatId, installation, command, project, idle, sendMessage }) {
  const handled = useRef(new Set())
  const scope = useRef(chatId)
  const latest = useRef(null)
  const check = useRef(null)
  const [recovery, setRecovery] = useState(null)
  latest.current = {chatId, command, project, idle, sendMessage}
  useEffect(() => () => check.current?.abort(), [chatId, command, project])
  const dispatch = useCallback(record => {
    const context = latest.current
    if (!context.idle || context.chatId !== record.chatId || context.command !== record.command || context.project !== record.project) return
    const key = JSON.stringify([record.chatId, record.requestId, record.target, record.project])
    if (handled.current.has(key) || readIntegrationRecovery(record.chatId)?.phase === 'dispatched') return
    if (!saveIntegrationRecovery({...record, phase: 'dispatched'})) {
      setRecovery({...record, error: 'Could not save continuation state. No request was sent.'})
      return
    }
    handled.current.add(key)
    setRecovery(null)
    void context.sendMessage(integrationRequest({...record, state: 'succeeded'}, record.command, record.project, record.chatId), integrationRequestId(record))
  }, [])
  useEffect(() => {
    if (scope.current !== chatId) { handled.current.clear(); scope.current = chatId }
    let record = readIntegrationRecovery(chatId)
    const eligible = ['pending', 'succeeded'].includes(installation?.state) &&
      integrationRequest({...installation, state: 'succeeded'}, command, project, chatId)
    if (eligible) {
      if (record?.requestId !== installation.requestId) {
        record = {version: 1, chatId, command, project, target: installation.target,
          requestId: installation.requestId, phase: 'pending'}
        if (!saveIntegrationRecovery(record)) {
          setRecovery({...record, error: 'Could not save continuation state. No request was sent.'})
          return
        }
      }
      if (installation.state === 'succeeded' && record.phase === 'pending') dispatch(record)
      return
    }
    // Reading historical conversations never starts a model or host action.
    setRecovery(record?.phase === 'pending' && record.command === command && record.project === project ? record : null)
  }, [chatId, installation, command, project, idle, sendMessage, dispatch])
  const resume = useCallback(async () => {
    const context = latest.current
    const record = readIntegrationRecovery(context.chatId)
    if (!context.idle || record?.phase !== 'pending' || record.command !== context.command || record.project !== context.project || check.current) return
    const controller = new AbortController()
    check.current = controller
    setRecovery({...record, checking: true})
    const timeout = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch(`/api/extensions/${record.target}/install-plan`, {signal: controller.signal, cache: 'no-store'})
      if (!response.ok || !readyIntegrationPlan(await response.json(), record.target)) throw new Error('not ready')
      if (!controller.signal.aborted) dispatch(record)
    } catch {
      if (latest.current.chatId === record.chatId && latest.current.command === record.command && latest.current.project === record.project) {
        setRecovery({...record, error: 'Extension readiness could not be confirmed. No new installation was requested.'})
      }
    } finally { clearTimeout(timeout); if (check.current === controller) check.current = null }
  }, [dispatch])
  return {recovery, resume}
}
