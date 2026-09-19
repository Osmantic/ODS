// Dashboard extension template — registration entrypoint (.js, no JSX).
//
// Copy this file and dashboard-plugin-template-page.jsx into:
//   extensions/services/dashboard/src/plugins/<your-plugin>/
// then load the entry by adding to extensions/services/dashboard/src/main.jsx:
//   import './plugins/<your-plugin>/dashboard-plugin-template'
//
// Convention (see src/plugins/core.js): registration lives in .js files;
// page components live in .jsx files and are lazy-imported. @vitejs/plugin-react
// only transforms JSX in .jsx/.tsx files, so a .js file containing JSX fails
// to parse at build time.

import { lazy } from 'react'
import { Sparkles } from 'lucide-react'
import { registerRoutes, registerExternalLinks } from '../registry'

const MyExtensionPage = lazy(() => import('./dashboard-plugin-template-page'))

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
