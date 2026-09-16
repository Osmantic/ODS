import {useLayoutEffect, useRef, useState} from 'react'

export function usePixelAutoScroll(messages, conversationId, endRef) {
  const following = useRef(true)
  const previousChat = useRef(conversationId)
  const [showLatest, setShowLatest] = useState(false)

  function jumpToLatest() {
    const container = endRef.current?.parentElement
    if (!container) return
    following.current = true
    container.scrollTop = container.scrollHeight
    container.focus({preventScroll:true})
    setShowLatest(false)
  }

  function onScroll(event) {
    const container = event.currentTarget
    following.current = container.scrollHeight - container.clientHeight - container.scrollTop <= 64
    setShowLatest(!following.current)
  }

  useLayoutEffect(() => {
    if (previousChat.current !== conversationId) {
      previousChat.current = conversationId
      following.current = true
      setShowLatest(false)
    }
    const container = endRef.current?.parentElement
    if (following.current && container) container.scrollTop = container.scrollHeight
  }, [messages, conversationId, endRef])

  return {onScroll, jumpToLatest, showLatest}
}
