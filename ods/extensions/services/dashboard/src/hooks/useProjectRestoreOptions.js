import {useCallback, useEffect, useMemo, useState} from 'react'
import {finishedRuns, restoreOptions} from '../lib/projectRestore'

// Which finished Portal turns of this chat offer "Put back files", asked
// again after each turn and each restore, never while Portal works. A
// question that fails offers nothing, calmly: no error for a missing button.
export default function useProjectRestoreOptions(chatId, messages, busy) {
  const key = useMemo(() => finishedRuns(messages).join(','), [messages])
  const [state, setState] = useState({chatId, entries: []})
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (busy || !key) return undefined
    const controller = new AbortController()
    restoreOptions(key.split(','), controller.signal).then(
      entries => { if (!controller.signal.aborted) setState({chatId, entries}) },
      () => { if (!controller.signal.aborted) setState({chatId, entries: []}) })
    return () => controller.abort()
  }, [chatId, key, busy, version])
  const entries = state.chatId === chatId && key ? state.entries : []
  const forRun = useCallback(runId => entries.filter(entry => entry.snapshot === runId), [entries])
  const refresh = useCallback(() => setVersion(value => value + 1), [])
  return {forRun, refresh}
}
