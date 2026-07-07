// Dashboard extension template.
// Copy this file and import it from your plugin entrypoint.

import { Sparkles } from 'lucide-react'
import { registerRoutes, registerExternalLinks } from '../../dashboard/src/plugins/registry'

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
