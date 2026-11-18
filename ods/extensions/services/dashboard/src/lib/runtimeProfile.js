// Pure helper: which runtime this dashboard is served by, from /api/status.
// "windows-desktop" is dashboard-api running natively on Windows with its own
// llama-server: no Docker, no WSL, no host agent and no Portal agent stack.
export const WINDOWS_DESKTOP = 'windows-desktop'

export function isWindowsDesktop(status) {
  return status?.runtimeProfile === WINDOWS_DESKTOP
}
