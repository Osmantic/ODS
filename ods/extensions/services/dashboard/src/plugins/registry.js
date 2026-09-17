import { coreRoutes, coreExternalLinks } from './core'
import { appendPath } from '../lib/serviceUrls'
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

export function getSidebarExternalLinks(context = {}) {
  const { status, getExternalUrl, apiLinks = [] } = context
  // Merge static plugin links with API-fetched links
  const allLinks = [...coreExternalLinks, ...externalLinkExtensions, ...apiLinks]
  // Merge by id so API values take priority without discarding static launcher
  // behavior such as OpenCode's always-visible application entry.
  const linksById = new Map()
  for (const link of allLinks) {
    linksById.set(link.id, { ...(linksById.get(link.id) || {}), ...link })
  }
  return [...linksById.values()].map(link => {
    const healthy = link.alwaysHealthy ? true : isServiceHealthy(status, link.healthNeedles || [])
    return {
      key: link.id,
      label: link.label,
      icon: typeof link.icon === 'string' ? (ICON_MAP[link.icon] || ExternalLink) : (link.icon || ExternalLink),
      healthy,
      alwaysVisible: Boolean(link.alwaysVisible),
      url: link.public_url || appendPath(
        typeof getExternalUrl === 'function' ? getExternalUrl(link.port) : `http://localhost:${link.port}`,
        link.ui_path,
      ),
    }
  })
}
