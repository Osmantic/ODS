import { lazy } from 'react'
import {
  LayoutDashboard,
  Settings,
  Puzzle,
  Activity,
  Box,
  Network,
  Cloud,
  UserPlus,
  CreditCard,
  Code,
  Bot,
} from 'lucide-react'

const Dashboard = lazy(() => import('../pages/Dashboard'))
const SettingsPage = lazy(() => import('../pages/Settings'))
const Extensions = lazy(() => import('../pages/Extensions'))
const GPUMonitor = lazy(() => import('../pages/GPUMonitor'))
const Models = lazy(() => import('../pages/Models'))
const RemoteProvider = lazy(() => import('../pages/RemoteProvider'))
const ServiceMap = lazy(() => import('../pages/ServiceMap'))
const Invites = lazy(() => import('../pages/Invites'))
const Usage = lazy(() => import('../pages/Usage'))
const Pixel = lazy(() => import('../pages/Pixel'))

export const coreRoutes = [
  {
    id: 'dashboard',
    path: '/',
    label: 'Dashboard',
    icon: LayoutDashboard,
    component: Dashboard,
    getProps: ({ status, loading }) => ({ status, loading }),
    sidebar: true,
    order: 0,
  },
  {
    id: 'gpu-monitor',
    path: '/gpu',
    label: 'GPU Monitor',
    icon: Activity,
    component: GPUMonitor,
    getProps: () => ({}),
    // Route is always registered; sidebar entry only appears on multi-GPU systems
    sidebar: ({ status }) => (status?.gpu?.gpu_count || 1) > 1,
    order: 1,
  },
  {
    id: 'extensions',
    path: '/extensions',
    label: 'Extensions',
    icon: Puzzle,
    component: Extensions,
    getProps: () => ({}),
    sidebar: true,
    order: 2,
  },
  {
    id: 'integrations',
    path: '/extensions/integrations',
    label: 'Integrations',
    icon: Network,
    component: ServiceMap,
    getProps: () => ({}),
    sidebar: true,
    order: 2.1,
  },
  {
    id: 'models',
    path: '/models',
    label: 'Models',
    icon: Box,
    component: Models,
    getProps: () => ({}),
    sidebar: true,
    order: 3,
  },
  {
    id: 'remote-provider',
    path: '/remote-provider',
    label: 'Remote GPU',
    icon: Cloud,
    component: RemoteProvider,
    getProps: () => ({}),
    sidebar: true,
    order: 3.2,
  },
  // Usage + Setup / Owner are reachable from Settings rather than the top-level
  // sidebar. Setup / Owner is a factory/distributor/service-provider flow, not
  // a day-to-day dashboard surface. Direct URLs still work for bookmarks and
  // for the magic-link redemption page which renders inside this dashboard.
  {
    id: 'usage',
    path: '/usage',
    label: 'Usage',
    icon: CreditCard,
    component: Usage,
    getProps: ({ status }) => ({ status }),
    sidebar: false,
    order: 3.5,
  },
  {
    id: 'pixel',
    path: '/pixel',
    label: 'Pixel',
    icon: Bot,
    component: Pixel,
    getProps: ({ status }) => ({ systemStatus: status }),
    sidebar: true,
    order: 0.5,
  },
  {
    id: 'invites',
    path: '/invites',
    label: 'Setup / Owner',
    icon: UserPlus,
    component: Invites,
    getProps: () => ({}),
    sidebar: false,
    order: 4,
  },
  {
    id: 'settings',
    path: '/settings',
    label: 'Settings',
    icon: Settings,
    component: SettingsPage,
    getProps: () => ({}),
    sidebar: true,
    order: 99,
  },
]

// OpenCode is an ODS application on every platform, even though its process is
// host-managed rather than part of the Docker stack. Keep the launcher present
// while health data is loading or the host service needs attention.
export const coreExternalLinks = [
  {
    id: 'opencode',
    label: 'OpenCode',
    icon: Code,
    port: 3003,
    ui_path: '/',
    healthNeedles: ['opencode', 'OpenCode (IDE)'],
    alwaysVisible: true,
  },
]
