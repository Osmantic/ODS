import { coreRoutes, coreExternalLinks } from './core'
import { appendPath, isLoopbackBrowser } from '../lib/serviceUrls'
import {
  MessageSquare, Network, Bot, Terminal, Search, Image, Code, ExternalLink
} from 'lucide-react'

const ICON_MAP = {
  MessageSquare, Network, Bot, Terminal, Search, Image, Code, ExternalLink,
}

const routeExtensions = []
const externalLinkExtensions = []

export function registerRoutes(routes = []) {
  routeExtensions.push(...routes)
}

export function registerExternalLinks(links = []) {
  externalLinkExtensions.push(...links)
}

export function getInternalRoutes(context = {}) {
  const allRoutes = [...coreRoutes, ...routeExtensions]
  return allRoutes
    .filter(route => (typeof route.enabled === 'function' ? route.enabled(context) : true))
    .sort((a, b) => (a.order || 0) - (b.order || 0))
}

export function getSidebarNavItems(context = {}) {
  return getInternalRoutes(context)
    .filter(route => {
      if (typeof route.sidebar === 'function') return route.sidebar(context)
      return route.sidebar !== false
    })
    .map(route => ({
      id: route.id,
      path: route.path,
      label: route.label,
      icon: route.icon,
    }))
}

function isServiceHealthy(status, needles = []) {
  const services = status?.services || []
  return needles.some(needle =>
    services.some(s => (s.name || '').toLowerCase().includes(needle.toLowerCase()) && s.status === 'healthy')
  )
}

// Host applications report a lifecycle through their service status:
// degraded = starting or being set up, down = installed but stopped, and
// not_deployed = never set up (the entry stays hidden, like other apps).
const APP_LIFECYCLE = { healthy: 'running', degraded: 'starting', down: 'stopped' }
const APP_STATE_LABELS = { starting: 'Starting', stopped: 'Stopped' }

function findService(status, link) {
  const services = status?.services || []
  const needles = (link.healthNeedles || []).map(needle => needle.toLowerCase())
  return services.find(service => service.id === link.id)
    || services.find(service => needles.some(needle => (service.name || '').toLowerCase().includes(needle)))
}

function withAppLifecycle(entry, link, status) {
  if (!link.appPath) return entry
  const state = entry.healthy ? 'running' : (APP_LIFECYCLE[findService(status, link)?.status] || 'not_installed')
  const directUrl = link.public_url || (link.loopbackOnly && !isLoopbackBrowser() ? null : entry.url)
  return {
    ...entry,
    state,
    visible: state !== 'not_installed',
    url: directUrl,
    internalPath: state === 'running' && directUrl ? null : link.appPath,
    stateLabel: APP_STATE_LABELS[state] || null,
  }
}

export function getSidebarExternalLinks(context = {}) {
  const { status, getExternalUrl, apiLinks = [] } = context
  // Merge static plugin links with API-fetched links
  const allLinks = [...coreExternalLinks, ...externalLinkExtensions, ...apiLinks]
  // Merge by id so API values take priority without discarding static launcher
  // behavior such as OpenCode's lifecycle-aware application entry.
  const linksById = new Map()
  for (const link of allLinks) {
    linksById.set(link.id, { ...(linksById.get(link.id) || {}), ...link })
  }
  return [...linksById.values()].map(link => {
    const healthy = link.alwaysHealthy ? true : isServiceHealthy(status, link.healthNeedles || [])
    return withAppLifecycle({
      key: link.id,
      label: link.label,
      icon: typeof link.icon === 'string' ? (ICON_MAP[link.icon] || ExternalLink) : (link.icon || ExternalLink),
      healthy,
      alwaysVisible: Boolean(link.alwaysVisible),
      url: link.public_url || appendPath(
        typeof getExternalUrl === 'function' ? getExternalUrl(link.port) : `http://localhost:${link.port}`,
        link.ui_path,
      ),
    }, link, status)
  })
}
