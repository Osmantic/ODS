// Dashboard extension template.
//
// Copy this file into the dashboard source tree (or your own plugin package)
// and import it from your plugin entrypoint.
//
// Keep the .jsx extension. @vitejs/plugin-react only transforms .jsx and .tsx,
// so JSX inside a .js file fails to parse at build time. For a plugin big
// enough to want its pages in separate files, follow src/plugins/core.js
// instead: keep registration in .js and lazy-import the .jsx page component.

import { Sparkles } from 'lucide-react'

// Adjust to wherever you place this file. The registry lives at
// extensions/services/dashboard/src/plugins/registry.js — from a plugin folder
// under src/, that is '../plugins/registry'.
import { registerRoutes, registerExternalLinks } from '../plugins/registry'

function MyExtensionPage() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold text-white">My Extension</h1>
      <p className="text-zinc-400 mt-2">Replace with your extension UI.</p>
    </div>
  )
}

registerRoutes([
  {
    id: 'my-extension',
    path: '/my-extension',
    label: 'My Extension',
    icon: Sparkles,
    component: MyExtensionPage,
    getProps: () => ({}),
    sidebar: true,
    order: 100,
  },
])

registerExternalLinks([
  {
    id: 'my-service-link',
    label: 'My Service',
    icon: Sparkles,
    port: 1234,
    healthNeedles: ['my service'],
  },
])
