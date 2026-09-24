// Dashboard extension template.
//
// Copy this file into the dashboard's plugin directory and import it from your
// plugin entrypoint:
//
//   cp extensions/templates/dashboard-plugin-template.jsx \
//      extensions/services/dashboard/src/plugins/my-extension.jsx
//
// It is a .jsx file because it defines a component inline: vite only applies
// the React/JSX transform to .jsx and .tsx, so JSX in a .js file fails to
// parse at build time. The `./registry` import below resolves once the file
// sits next to registry.js in src/plugins/ (see core.js for the same imports).

import { Sparkles } from 'lucide-react'
import { registerRoutes, registerExternalLinks } from './registry'

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
