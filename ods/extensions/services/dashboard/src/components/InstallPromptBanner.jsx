// Smart PWA install prompt banner.
//
// Slides in from the bottom on small screens, sits in the bottom-right
// on desktop. Only renders when usePwaInstallPrompt says the moment is
// right (3+ visits, not dismissed, not already installed, browser is
// installable — or iOS where the install path is manual).
//
// Two button states:
//   * non-iOS: "Add to home screen" -> programmatic prompt
//   * iOS: copy reads "Tap Share → Add to Home Screen" with the
//     Share icon called out; no programmatic prompt is possible
//     (Safari refuses to expose it).

import { Smartphone, X, Share, Plus } from 'lucide-react'
import { usePwaInstallPrompt } from '../hooks/usePwaInstallPrompt'

export default function InstallPromptBanner() {
  const { shouldShow, isIos, promptInstall, dismiss } = usePwaInstallPrompt()
  if (!shouldShow) return null

  return (
    <div
      role="dialog"
      aria-label="Add ODS to your home screen"
      className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 md:max-w-sm
                 bg-theme-card border border-theme-accent/40 rounded-xl shadow-2xl
                 p-4 z-40 animate-in slide-in-from-bottom-4 fade-in duration-300"
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-xl bg-theme-accent/15 text-theme-accent flex items-center justify-center flex-shrink-0">
          <Smartphone size={22} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-theme-text text-sm mb-1">
            Make ODS feel like an app
          </h3>
          {isIos ? (
            <p className="text-xs text-theme-text-muted leading-relaxed">
              Tap{' '}
              <Share size={12} className="inline align-text-bottom mx-0.5 text-theme-accent" />
              <strong className="text-theme-text">Share</strong>, then{' '}
              <strong className="text-theme-text">Add to Home Screen</strong>{' '}
              to put ODS a tap away.
            </p>
          ) : (
            <p className="text-xs text-theme-text-muted leading-relaxed">
              Install ODS as an app on this device for one-tap access — no browser tabs, no typing the address.
            </p>
          )}
        </div>
        <button
          onClick={dismiss}
          aria-label="Dismiss"
          className="text-theme-text-muted hover:text-theme-text p-1 -mr-1 -mt-1 flex-shrink-0"
        >
          <X size={16} />
        </button>
      </div>

      {!isIos && (
        <div className="flex items-center gap-2 mt-3">
          <button
            onClick={promptInstall}
            className="flex-1 flex items-center justify-center gap-2 bg-theme-accent text-white text-sm py-2 px-4 rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus size={16} />
            Add to home screen
          </button>
          <button
            onClick={dismiss}
            className="text-xs text-theme-text-muted hover:text-theme-text px-3 py-2"
          >
            Not now
          </button>
        </div>
      )}
    </div>
  )
}
