import { lazy } from 'react'
import {
  LayoutDashboard,
  Settings,
  Puzzle,
  Activity,
  Box,
  Network,
  UserPlus,
  CreditCard,
} from 'lucide-react'

const Dashboard = lazy(() => import('../pages/Dashboard'))
const SettingsPage = lazy(() => import('../pages/Settings'))
const Extensions = lazy(() => import('../pages/Extensions'))
const GPUMonitor = lazy(() => import('../pages/GPUMonitor'))
const Models = lazy(() => import('../pages/Models'))
const ServiceMap = lazy(() => import('../pages/ServiceMap'))
const Invites = lazy(() => import('../pages/Invites'))
const Usage = lazy(() => import('../pages/Usage'))

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
    id: 'usage',
    path: '/usage',
    label: 'Usage',
    icon: CreditCard,
    component: Usage,
    getProps: ({ status }) => ({ status }),
    sidebar: true,
    order: 3.5,
  },
  {
    id: 'invites',
    path: '/invites',
    label: 'Invites',
    icon: UserPlus,
    component: Invites,
    getProps: () => ({}),
    sidebar: true,
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

export const coreExternalLinks = []
