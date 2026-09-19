import {useRef} from 'react'
import {Keyboard, X} from 'lucide-react'

export default function PixelKeyboardHelp({sendMode}) {
  const dialog = useRef(null)
  const trigger = useRef(null)
  const closeButton = useRef(null)
  const shortcuts = [
    ['Send a message', sendMode === 'mod-enter' ? 'Ctrl/⌘ + Enter' : 'Enter'],
    ['Add a new line', sendMode === 'mod-enter' ? 'Enter or Shift + Enter' : 'Shift + Enter'],
    ['Search conversations and actions', 'Ctrl/⌘ + K'],
    ['Choose a search result', '↑ / ↓, then Enter'],
    ['Open prompt commands', 'Type / in an empty draft'],
    ['Move between prompt commands', '↑ / ↓ or Home / End'],
    ['Close search, commands or this help', 'Escape'],
  ]
  return <>
    <button ref={trigger} type="button" aria-label="Keyboard shortcuts" title="Keyboard shortcuts" onClick={() => {dialog.current.showModal(); closeButton.current.focus()}}><Keyboard size={16}/></button>
    <dialog ref={dialog} aria-label="Pixel keyboard shortcuts" className="pixel-command-dialog max-h-[80dvh] overflow-y-auto p-4" onClose={() => trigger.current?.focus()} onClick={event => {if (event.target === event.currentTarget) dialog.current.close()}}>
      <div className="mb-3 flex items-center justify-between gap-4"><h2 className="font-semibold">Keyboard shortcuts</h2><button ref={closeButton} type="button" aria-label="Close keyboard shortcuts" onClick={() => dialog.current.close()}><X size={18}/></button></div>
      <table className="w-full text-left text-sm"><thead><tr><th scope="col" className="pb-2">Action</th><th scope="col" className="pb-2">Keys</th></tr></thead><tbody>{shortcuts.map(([action, keys]) => <tr key={action}><th scope="row" className="py-2 pr-4 font-normal">{action}</th><td className="py-2"><kbd>{keys}</kbd></td></tr>)}</tbody></table>
      <p className="mt-3 text-xs text-theme-text-muted">The send key follows your Send shortcut setting in the task menu. Speech dictation and sending still require their own actions.</p>
    </dialog>
  </>
}
