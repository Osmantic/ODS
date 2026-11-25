import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  CornerDownLeft,
  ExternalLink,
  FileText,
  FolderOpen,
  Hash,
  Info,
  Lightbulb,
  Search,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react'
import ODSLogo from '../components/ODSLogo'
import './docs.css'

const DEFAULT_SLUG = 'getting-started/overview'

function extractHeadingText(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractHeadingText).join('')
  if (node && typeof node === 'object' && node.props?.children !== undefined) {
    return extractHeadingText(node.props.children)
  }
  return ''
}

function slugifyHeading(node) {
  return extractHeadingText(node)
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[-\s]+/g, '-')
}

function HighlightText({ text, query }) {
  if (!text) return null
  const cleanQuery = (query || '').trim()
  if (cleanQuery.length < 2) return text

  const escaped = cleanQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = String(text).split(new RegExp(`(${escaped})`, 'ig'))
  if (parts.length > 1) {
    return parts.map((part, idx) =>
      part.toLowerCase() === cleanQuery.toLowerCase()
        ? <mark key={idx} className="docs-search-highlight">{part}</mark>
        : part
    )
  }

  const tokens = cleanQuery.split(/\s+/).filter(t => t.length >= 2)
  if (tokens.length <= 1) return text
  const tokenRegex = new RegExp(`(${tokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  const tokenParts = String(text).split(tokenRegex)
  const tokenSet = new Set(tokens.map(t => t.toLowerCase()))
  return tokenParts.map((part, idx) =>
    tokenSet.has(part.toLowerCase())
      ? <mark key={idx} className="docs-search-highlight">{part}</mark>
      : part
  )
}

// Helper to determine callout type from markdown blockquote
function parseCalloutType(text) {
  if (typeof text !== 'string') return null
  const trimmed = text.trim()
  const match = trimmed.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/i)
  return match ? match[1].toLowerCase() : null
}

const CALLOUT_ICONS = {
  note: Info,
  tip: Lightbulb,
  important: AlertCircle,
  warning: AlertTriangle,
  caution: ShieldAlert,
}

// Code Block with Copy Button and Language Badge
function DocsCodeBlock({ language, code }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }, [code])

  return (
    <div className="docs-code-container">
      <div className="docs-code-header">
        <span className="uppercase font-semibold tracking-wider text-[11px]">
          {language || 'text'}
        </span>
        <button
          className="docs-code-copy-btn"
          onClick={handleCopy}
          aria-label={copied ? 'Copied code' : 'Copy code to clipboard'}
        >
          {copied ? (
            <>
              <Check size={12} className="text-emerald-400" />
              <span className="text-emerald-400">Copied</span>
            </>
          ) : (
            <>
              <Copy size={12} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="docs-code-pre">
        <code>{code}</code>
      </pre>
    </div>
  )
}

// Resolve any markdown href (relative paths, .md files, anchors, external URLs)
function resolveMarkdownLink(href, pathMap) {
  if (!href) return null

  // External URLs (http, https, mailto, etc.)
  if (/^(https?:|\/\/|mailto:|tel:)/i.test(href)) {
    return { type: 'external', href }
  }

  // Pure in-page anchor link (#section)
  if (href.startsWith('#')) {
    return { type: 'anchor', href }
  }

  // Split path target and #hash
  const [rawTarget, rawHash] = href.split('#')
  const hashSuffix = rawHash ? `#${rawHash}` : ''

  // Clean target path: strip leading ./, ../, and leading /
  let clean = (rawTarget || '').trim().replace(/\\/g, '/')
  if (clean.startsWith('/docs/')) {
    clean = clean.replace(/^\/docs\//, '')
  } else {
    // Strip leading ./ and any occurrences of ../
    clean = clean.replace(/^(\.\.?\/)+/, '').replace(/^\//, '')
  }

  const cleanLower = clean.toLowerCase()
  const cleanNoMd = cleanLower.replace(/\.(md|markdown)$/i, '')
  const fileName = cleanLower.split('/').pop() || ''
  const fileNameNoMd = fileName.replace(/\.(md|markdown)$/i, '')

  // Try lookups in pathMap:
  // 1. Exact clean path (e.g. "docs/macos-quickstart.md")
  // 2. Clean path without .md (e.g. "docs/macos-quickstart")
  // 3. Basename filename (e.g. "macos-quickstart.md")
  // 4. Basename without .md (e.g. "macos-quickstart")
  const targetSlug =
    pathMap.get(cleanLower) ||
    pathMap.get(cleanNoMd) ||
    pathMap.get(fileName) ||
    pathMap.get(fileNameNoMd)

  if (targetSlug) {
    return { type: 'internal', to: `/docs/${targetSlug}${hashSuffix}` }
  }

  // Fallback for markdown files or docs paths not in registry
  if (cleanLower.endsWith('.md') || cleanLower.startsWith('docs/')) {
    let slugFallback = cleanNoMd.startsWith('docs/') ? cleanNoMd.replace(/^docs\//, '') : cleanNoMd
    // Ensure slugFallback strictly conforms to the [category]/[document] format
    if (!slugFallback.includes('/')) {
      const matchedSlug = pathMap.get(slugFallback.toLowerCase())
      if (matchedSlug && matchedSlug.includes('/')) {
        return { type: 'internal', to: `/docs/${matchedSlug}${hashSuffix}` }
      }
      slugFallback = `getting-started/${slugFallback}`
    }
    return { type: 'internal', to: `/docs/${slugFallback}${hashSuffix}` }
  }

  // Internal absolute app route
  if (rawTarget.startsWith('/')) {
    return { type: 'internal', to: `${rawTarget}${hashSuffix}` }
  }

  // Fallback as external/raw
  return { type: 'external', href }
}

export default function Docs() {
  const location = useLocation()
  const navigate = useNavigate()
  const params = useParams()

  const [catalog, setCatalog] = useState([])
  const [pathMapData, setPathMapData] = useState({})
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [currentDoc, setCurrentDoc] = useState(null)
  const [docLoading, setDocLoading] = useState(true)
  const [docError, setDocError] = useState(null)

  const [collapsedCategories, setCollapsedCategories] = useState({})
  const [filterQuery, setFilterQuery] = useState('')
  const [searchModalOpen, setSearchModalOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchFilter, setSearchFilter] = useState('all')
  const [searchResults, setSearchResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [selectedSearchIdx, setSelectedSearchIdx] = useState(0)

  const [activeHeadingId, setActiveHeadingId] = useState('')
  const searchItemRefs = useRef({})

  // Determine target slug from path: /docs, /docs/:category/:slug
  const activeSlug = useMemo(() => {
    const clean = location.pathname.replace(/^\/docs\/?/, '').replace(/^\//, '').trim()
    return clean || DEFAULT_SLUG
  }, [location.pathname])

  // Build a reverse lookup map for relative link resolution
  const pathMap = useMemo(() => {
    const map = new Map()
    // 1. Server path map
    for (const [key, slug] of Object.entries(pathMapData || {})) {
      map.set(key.toLowerCase(), slug)
      const withoutMd = key.toLowerCase().replace(/\.(md|markdown)$/i, '')
      map.set(withoutMd, slug)
    }
    // 2. Client catalog documents fallback
    for (const cat of catalog || []) {
      for (const doc of cat.documents || []) {
        if (doc.slug) {
          const sLower = doc.slug.toLowerCase()
          map.set(sLower, doc.slug)
          const leaf = sLower.split('/').pop()
          if (leaf) map.set(leaf, doc.slug)
        }
        if (doc.path) {
          const norm = doc.path.replace(/\\/g, '/').toLowerCase()
          map.set(norm, doc.slug)
          const fname = norm.split('/').pop()
          if (fname) {
            map.set(fname, doc.slug)
            map.set(fname.replace(/\.(md|markdown)$/i, ''), doc.slug)
          }
        }
      }
    }
    return map
  }, [pathMapData, catalog])

  // Fetch catalog on mount
  useEffect(() => {
    let active = true
    fetch('/api/docs/catalog')
      .then(res => res.ok ? res.json() : Promise.reject(new Error('Failed to load documentation catalog')))
      .then(data => {
        if (!active) return
        setCatalog(data.categories || [])
        setPathMapData(data.path_map || {})
        setCatalogLoading(false)
      })
      .catch(err => {
        if (!active) return
        setCatalogLoading(false)
        setDocError(err.message)
      })
    return () => { active = false }
  }, [])

  // Fetch document whenever activeSlug changes
  useEffect(() => {
    let active = true
    setDocLoading(true)
    setDocError(null)

    fetch(`/api/docs/document?slug=${encodeURIComponent(activeSlug)}`)
      .then(res => {
        if (!res.ok) {
          throw new Error(res.status === 404 ? 'Document not found' : 'Failed to load document')
        }
        return res.json()
      })
      .then(data => {
        if (!active) return
        setCurrentDoc(data)
        setDocLoading(false)
        // If the URL slug was an alias or relative path (e.g. docs/MACOS-QUICKSTART.md)
        // and doesn't match the canonical slug, normalize the browser URL cleanly
        if (data.slug && activeSlug !== data.slug && activeSlug !== DEFAULT_SLUG) {
          navigate(`/docs/${data.slug}${location.hash || ''}`, { replace: true })
        }
        if (!location.hash) {
          window.scrollTo({ top: 0, behavior: 'smooth' })
        }
      })
      .catch(err => {
        if (!active) return
        setDocError(err.message)
        setDocLoading(false)
      })

    return () => { active = false }
  }, [activeSlug, navigate])

  // Smoothly scroll to in-file #section anchor once document renders
  useEffect(() => {
    if (docLoading || !currentDoc || !location.hash) return
    const targetId = decodeURIComponent(location.hash.slice(1))
    if (!targetId) return
    const frame = window.requestAnimationFrame(() => {
      const el = document.getElementById(targetId)
      if (el) {
        if (typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
        setActiveHeadingId(targetId)
      }
    })
    return () => window.cancelAnimationFrame(frame)
  }, [docLoading, currentDoc, location.hash])

  // Global Ctrl+K / Cmd+K listener for search
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchModalOpen(prev => !prev)
      }
      if (e.key === 'Escape') {
        setSearchModalOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Search API fetch with debounce
  useEffect(() => {
    if (!searchModalOpen || searchQuery.trim().length < 2) {
      setSearchResults([])
      setSelectedSearchIdx(0)
      return
    }

    const timer = setTimeout(() => {
      setSearching(true)
      fetch(`/api/docs/search?q=${encodeURIComponent(searchQuery.trim())}`)
        .then(res => res.ok ? res.json() : { results: [] })
        .then(data => {
          setSearchResults(data.results || [])
          setSelectedSearchIdx(0)
          setSearching(false)
        })
        .catch(() => setSearching(false))
    }, 180)

    return () => clearTimeout(timer)
  }, [searchQuery, searchModalOpen])

  const quickDocs = useMemo(() => {
    const list = []
    for (const cat of catalog) {
      for (const doc of cat.documents || []) {
        list.push({ ...doc, category_title: cat.title })
        if (list.length >= 6) return list
      }
    }
    return list
  }, [catalog])

  // Reset filter & selection when modal opens/closes or query/filter changes
  useEffect(() => {
    if (!searchModalOpen) {
      setSearchFilter('all')
    }
    setSelectedSearchIdx(0)
  }, [searchModalOpen, searchQuery, searchFilter])

  const filteredSearchResults = useMemo(() => {
    if (searchFilter === 'title') {
      return searchResults.filter(r => r.match_type !== 'section')
    }
    if (searchFilter === 'section') {
      return searchResults.filter(r => (r.sections || []).length > 0)
    }
    return searchResults
  }, [searchResults, searchFilter])

  const searchCounts = useMemo(() => ({
    all: searchResults.length,
    title: searchResults.filter(r => r.match_type !== 'section').length,
    section: searchResults.reduce((acc, r) => acc + (r.sections?.length || 0), 0),
  }), [searchResults])

  const navItems = useMemo(() => {
    const items = []
    if (searchQuery.trim().length >= 2) {
      for (const res of filteredSearchResults) {
        const docTarget = `/docs/${res.slug}`
        if (searchFilter !== 'section') {
          items.push({ key: `doc:${res.slug}`, to: docTarget })
        }
        for (const sec of res.sections || []) {
          const secTarget = `/docs/${res.slug}${sec.anchor ? `#${sec.anchor}` : ''}`
          items.push({ key: `sec:${res.slug}#${sec.anchor || sec.heading}`, to: secTarget })
        }
      }
    } else {
      for (const doc of quickDocs) {
        items.push({ key: `quick:${doc.slug}`, to: `/docs/${doc.slug}` })
      }
      for (const cat of catalog) {
        const firstSlug = cat.documents?.[0]?.slug
        if (firstSlug) {
          items.push({ key: `cat:${cat.id}`, to: `/docs/${firstSlug}` })
        }
      }
    }
    return items
  }, [searchQuery, filteredSearchResults, searchFilter, quickDocs, catalog])

  useEffect(() => {
    const activeItem = navItems[selectedSearchIdx]
    const el = activeItem ? searchItemRefs.current[activeItem.key] : null
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedSearchIdx, navItems])

  // Modal keyboard navigation across results, sections, Quick Jump, and Categories
  useEffect(() => {
    if (!searchModalOpen) return
    function handleModalNav(e) {
      if (!navItems.length) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedSearchIdx(prev => (prev + 1) % navItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedSearchIdx(prev => (prev - 1 + navItems.length) % navItems.length)
      } else if (e.key === 'ArrowRight' && searchQuery.length === 0) {
        e.preventDefault()
        setSelectedSearchIdx(prev => (prev + 1) % navItems.length)
      } else if (e.key === 'ArrowLeft' && searchQuery.length === 0) {
        e.preventDefault()
        setSelectedSearchIdx(prev => (prev - 1 + navItems.length) % navItems.length)
      } else if (e.key === 'Enter') {
        const activeEl = document.activeElement
        if (
          activeEl &&
          (activeEl.getAttribute('role') === 'tab' ||
            activeEl.classList.contains('docs-modal-clear-btn') ||
            activeEl.classList.contains('docs-modal-esc-kbd'))
        ) {
          return
        }
        const selected = navItems[selectedSearchIdx]
        if (selected) {
          e.preventDefault()
          navigate(selected.to)
          setSearchModalOpen(false)
        }
      }
    }
    window.addEventListener('keydown', handleModalNav)
    return () => window.removeEventListener('keydown', handleModalNav)
  }, [searchModalOpen, navItems, selectedSearchIdx, searchQuery, navigate])

  // Throttled Scroll-spy for Table of Contents using requestAnimationFrame to prevent layout thrashing
  useEffect(() => {
    if (!currentDoc?.headings?.length) return

    let rafId = null
    const handleScroll = () => {
      if (rafId) return
      rafId = window.requestAnimationFrame(() => {
        rafId = null
        const headingElements = currentDoc.headings
          .map(h => document.getElementById(h.id))
          .filter(Boolean)

        const scrollPos = window.scrollY + 100
        let currentId = ''

        for (const el of headingElements) {
          if (el.offsetTop <= scrollPos) {
            currentId = el.id
          } else {
            break
          }
        }

        if (currentId) setActiveHeadingId(currentId)
      })
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    handleScroll()
    return () => {
      window.removeEventListener('scroll', handleScroll)
      if (rafId) window.cancelAnimationFrame(rafId)
    }
  }, [currentDoc])

  const toggleCategory = useCallback((categoryId) => {
    setCollapsedCategories(prev => ({
      ...prev,
      [categoryId]: !prev[categoryId],
    }))
  }, [])

  // Markdown custom renderers
  const markdownComponents = useMemo(() => ({
    // Code blocks
    code({ inline, className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || '')
      const codeString = String(children).replace(/\n$/, '')
      if (!inline && match) {
        return <DocsCodeBlock language={match[1]} code={codeString} />
      }
      if (!inline && codeString.includes('\n')) {
        return <DocsCodeBlock language="text" code={codeString} />
      }
      return <code className="docs-inline-code" {...props}>{children}</code>
    },
    // Heading anchors
    h1: ({ children }) => {
      const id = slugifyHeading(children)
      return <h1 id={id || undefined} className="docs-h1">{children}</h1>
    },
    h2: ({ children }) => {
      const id = slugifyHeading(children)
      return (
        <h2 id={id} className="docs-h2 group flex items-center">
          <span className="flex-1">{children}</span>
          <a href={`#${id}`} className="opacity-0 group-hover:opacity-60 ml-2 text-theme-text-muted no-underline" aria-label="Link to section">#</a>
        </h2>
      )
    },
    h3: ({ children }) => {
      const id = slugifyHeading(children)
      return (
        <h3 id={id} className="docs-h3 group flex items-center">
          <span className="flex-1">{children}</span>
          <a href={`#${id}`} className="opacity-0 group-hover:opacity-60 ml-2 text-theme-text-muted no-underline" aria-label="Link to section">#</a>
        </h3>
      )
    },
    // Tables
    table: ({ children }) => (
      <div className="docs-table-wrapper">
        <table>{children}</table>
      </div>
    ),
    // Blockquote & GitHub Alerts
    blockquote: ({ children }) => {
      let rawText = ''
      if (Array.isArray(children)) {
        rawText = children.map(c => c?.props?.children || '').join(' ')
      } else if (children?.props?.children) {
        rawText = String(children.props.children)
      }

      const alertType = parseCalloutType(rawText)
      if (alertType && CALLOUT_ICONS[alertType]) {
        const IconComponent = CALLOUT_ICONS[alertType]
        const cleanChildren = Array.isArray(children) ? children.slice(1) : children
        return (
          <div className={`docs-callout docs-callout-${alertType}`}>
            <IconComponent size={18} className="shrink-0 mt-0.5" />
            <div className="docs-callout-body">
              <div className="docs-callout-title uppercase text-[11px] tracking-wider">
                {alertType}
              </div>
              <div>{cleanChildren}</div>
            </div>
          </div>
        )
      }

      return <blockquote>{children}</blockquote>
    },
    // Link rewriting for relative markdown files, anchors, and external links
    a: ({ href, children }) => {
      const resolved = resolveMarkdownLink(href, pathMap)
      if (!resolved) return <a>{children}</a>

      if (resolved.type === 'internal') {
        return <Link to={resolved.to}>{children}</Link>
      }

      if (resolved.type === 'anchor') {
        return <a href={resolved.href}>{children}</a>
      }

      return (
        <a href={resolved.href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1">
          <span>{children}</span>
          <ExternalLink size={12} className="inline opacity-60" />
        </a>
      )
    },
  }), [pathMap])

  // Memoize rendered markdown body so modal state / keystrokes / scrollspy never re-parse or re-highlight markdown
  const renderedMarkdown = useMemo(() => {
    if (!currentDoc?.content) return null
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={markdownComponents}
      >
        {currentDoc.content}
      </ReactMarkdown>
    )
  }, [currentDoc?.slug, currentDoc?.content, markdownComponents])

  return (
    <div className="docs-page-container">
      {/* Top Header */}
      <header className="docs-header">
        <div className="docs-header-left">
          <Link to="/" className="docs-header-title">
            <ODSLogo size={22} active />
            <span>ODS</span>
          </Link>
          <span className="docs-header-badge">Docs</span>
          <div className="docs-breadcrumbs hidden sm:flex">
            <span className="docs-breadcrumb-separator">/</span>
            <span>{currentDoc?.category_title || 'Documentation'}</span>
            {currentDoc?.title && (
              <>
                <span className="docs-breadcrumb-separator">/</span>
                <span className="docs-breadcrumb-active">{currentDoc.title}</span>
              </>
            )}
          </div>
        </div>

        <div className="docs-header-right">
          <button
            className="docs-search-trigger"
            onClick={() => setSearchModalOpen(true)}
            aria-label="Search documentation (Ctrl+K)"
          >
            <Search size={14} />
            <span className="hidden sm:inline">Search docs...</span>
            <span className="docs-kbd">Ctrl K</span>
          </button>

          <Link to="/" className="docs-btn-back">
            <ArrowLeft size={14} />
            <span>Portal</span>
          </Link>
        </div>
      </header>

      {/* Main Body Shell */}
      <div className="docs-body-shell">
        {/* Left Navigation Sidebar */}
        <aside className="docs-nav-sidebar" aria-label="Documentation Categories">
          <input
            type="search"
            placeholder="Filter documentation..."
            className="docs-nav-filter"
            value={filterQuery}
            onChange={e => setFilterQuery(e.target.value)}
          />

          {catalogLoading ? (
            <div className="p-4 text-xs text-theme-text-muted animate-pulse">Loading catalog...</div>
          ) : (
            catalog.map(category => {
              const matchingDocs = (category.documents || []).filter(doc =>
                doc.title.toLowerCase().includes(filterQuery.toLowerCase()) ||
                doc.description.toLowerCase().includes(filterQuery.toLowerCase())
              )

              if (filterQuery && matchingDocs.length === 0) return null
              const isCollapsed = collapsedCategories[category.id] && !filterQuery

              return (
                <div key={category.id} className="docs-category-group">
                  <div
                    className="docs-category-header"
                    onClick={() => toggleCategory(category.id)}
                    role="button"
                    tabIndex={0}
                  >
                    <span>{category.title}</span>
                    {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  </div>

                  {!isCollapsed && (
                    <div className="flex flex-col gap-0.5 pl-1">
                      {matchingDocs.map(doc => {
                        const isActive = activeSlug === doc.slug
                        return (
                          <NavLink
                            key={doc.slug}
                            to={`/docs/${doc.slug}`}
                            className={`docs-nav-item ${isActive ? 'is-active' : ''}`}
                            title={doc.title}
                          >
                            <FileText size={14} className="shrink-0 opacity-70" />
                            <span className="truncate">{doc.title}</span>
                          </NavLink>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </aside>

        {/* Center Article Area */}
        <main className="docs-content-area">
          <div className="docs-article-wrapper">
            {docLoading ? (
              <div className="p-12 animate-pulse flex flex-col gap-4">
                <div className="h-6 w-32 bg-theme-card rounded" />
                <div className="h-10 w-3/4 bg-theme-card rounded" />
                <div className="h-4 w-full bg-theme-card rounded mt-6" />
                <div className="h-4 w-5/6 bg-theme-card rounded" />
                <div className="h-4 w-4/6 bg-theme-card rounded" />
              </div>
            ) : docError ? (
              <div className="p-8 border border-red-500/30 rounded-xl bg-red-500/10 text-center">
                <AlertCircle size={36} className="mx-auto text-red-400 mb-3" />
                <h2 className="text-lg font-semibold text-red-300">Document Unavailable</h2>
                <p className="text-sm text-theme-text-muted mt-1">{docError}</p>
                <button
                  className="mt-4 px-4 py-2 bg-theme-card border border-theme-border rounded-lg text-sm"
                  onClick={() => navigate(`/docs/${DEFAULT_SLUG}`)}
                >
                  Return to Overview
                </button>
              </div>
            ) : currentDoc ? (
              <article>
                <header className="docs-article-header">
                  <div className="docs-article-category">
                    {currentDoc.category_title}
                  </div>
                  <h1 className="docs-article-title">{currentDoc.title}</h1>
                  <div className="docs-article-meta">
                    <span className="docs-meta-item">
                      <Clock size={14} />
                      <span>{currentDoc.reading_time_minutes} min read</span>
                    </span>
                    <span className="docs-meta-item">
                      <BookOpen size={14} />
                      <span>{currentDoc.word_count?.toLocaleString()} words</span>
                    </span>
                  </div>
                </header>

                <div className="docs-markdown-body">
                  {renderedMarkdown}
                </div>

                {/* Bottom Pagination */}
                <nav className="docs-pagination-nav" aria-label="Pagination">
                  {currentDoc.prev ? (
                    <Link to={`/docs/${currentDoc.prev.slug}`} className="docs-pagination-card prev">
                      <span className="docs-pagination-label">← Previous</span>
                      <span className="docs-pagination-title">{currentDoc.prev.title}</span>
                    </Link>
                  ) : <div />}

                  {currentDoc.next ? (
                    <Link to={`/docs/${currentDoc.next.slug}`} className="docs-pagination-card next">
                      <span className="docs-pagination-label">Next →</span>
                      <span className="docs-pagination-title">{currentDoc.next.title}</span>
                    </Link>
                  ) : <div />}
                </nav>
              </article>
            ) : null}
          </div>
        </main>

        {/* Right Table of Contents Sidebar */}
        <aside className="docs-toc-sidebar" aria-label="Table of Contents">
          {currentDoc?.headings && currentDoc.headings.length > 0 && (
            <>
              <div className="docs-toc-title">On this page</div>
              <ul className="docs-toc-list">
                {currentDoc.headings.map(h => (
                  <li key={h.id}>
                    <a
                      href={`#${h.id}`}
                      className={`docs-toc-link level-${h.level} ${activeHeadingId === h.id ? 'is-active' : ''}`}
                      title={h.text}
                    >
                      {h.text}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>

      {/* Global Search Modal */}
      {searchModalOpen && (
        <div
          className="docs-modal-backdrop"
          onClick={() => setSearchModalOpen(false)}
        >
          <div
            className="docs-search-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Search documentation"
          >
            <div className="docs-modal-input-row">
              <Search size={20} className="text-theme-accent-light shrink-0" />
              <input
                autoFocus
                type="text"
                placeholder="Search documentation (e.g. quickstart, GPU, switchboard)..."
                className="docs-modal-input"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="docs-modal-clear-btn"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                >
                  <X size={14} />
                </button>
              )}
              <button
                type="button"
                className="docs-modal-esc-kbd"
                onClick={() => setSearchModalOpen(false)}
                aria-label="Close search"
              >
                ESC
              </button>
            </div>

            {searchQuery.trim().length >= 2 && !searching && searchResults.length > 0 && (
              <div className="docs-modal-filter-bar">
                <div className="docs-modal-filter-tabs" role="tablist" aria-label="Filter search matches">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={searchFilter === 'all'}
                    className={`docs-modal-filter-tab ${searchFilter === 'all' ? 'is-active' : ''}`}
                    onClick={() => setSearchFilter('all')}
                  >
                    <span>All</span>
                    <span className="docs-modal-filter-count">({searchCounts.all})</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={searchFilter === 'title'}
                    className={`docs-modal-filter-tab ${searchFilter === 'title' ? 'is-active' : ''}`}
                    onClick={() => setSearchFilter('title')}
                  >
                    <span>Documents</span>
                    <span className="docs-modal-filter-count">({searchCounts.title})</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={searchFilter === 'section'}
                    className={`docs-modal-filter-tab ${searchFilter === 'section' ? 'is-active' : ''}`}
                    onClick={() => setSearchFilter('section')}
                  >
                    <span>In-file sections</span>
                    <span className="docs-modal-filter-count">({searchCounts.section})</span>
                  </button>
                </div>
              </div>
            )}

            <div className="docs-modal-results">
              {searching ? (
                <div className="p-8 text-xs text-theme-text-muted text-center">
                  Searching titles and document sections...
                </div>
              ) : filteredSearchResults.length > 0 ? (
                (() => {
                  const navIndexByKey = new Map(navItems.map((item, i) => [item.key, i]))
                  return filteredSearchResults.map(res => {
                    const docKey = `doc:${res.slug}`
                    const docNavIdx = navIndexByKey.get(docKey)
                    const isDocSelected = docNavIdx !== undefined && selectedSearchIdx === docNavIdx
                    const hasSectionSelected = (res.sections || []).some(sec => {
                      const sIdx = navIndexByKey.get(`sec:${res.slug}#${sec.anchor || sec.heading}`)
                      return sIdx !== undefined && selectedSearchIdx === sIdx
                    })

                    return (
                      <div
                        key={res.slug}
                        className={`docs-modal-result-card ${(isDocSelected || hasSectionSelected) ? 'has-active' : ''}`}
                      >
                        <div
                          ref={el => { searchItemRefs.current[docKey] = el }}
                          className={`docs-modal-result-item ${isDocSelected ? 'is-selected' : ''}`}
                          onClick={() => {
                            navigate(`/docs/${res.slug}`)
                            setSearchModalOpen(false)
                          }}
                          onMouseEnter={() => {
                            if (docNavIdx !== undefined) setSelectedSearchIdx(docNavIdx)
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <div className="docs-modal-result-icon">
                            <FileText size={16} />
                          </div>
                          <div className="docs-modal-result-body">
                            <div className="docs-modal-result-header">
                              <span className="docs-modal-result-title">
                                <HighlightText text={res.title} query={searchQuery} />
                              </span>
                              <div className="docs-modal-badges">
                                <span className="docs-modal-category-pill">{res.category_title}</span>
                              </div>
                            </div>
                            {res.description && (
                              <div className="docs-modal-result-desc">
                                <HighlightText text={res.description} query={searchQuery} />
                              </div>
                            )}
                          </div>
                          {isDocSelected && (
                            <CornerDownLeft size={14} className="text-theme-accent-light shrink-0 mt-1.5" />
                          )}
                        </div>

                        {res.sections && res.sections.length > 0 && (
                          <div className="docs-modal-sections">
                            {res.sections.map((sec, sIdx) => {
                              const secKey = `sec:${res.slug}#${sec.anchor || sec.heading}`
                              const secNavIdx = navIndexByKey.get(secKey)
                              const isSecSelected = secNavIdx !== undefined && selectedSearchIdx === secNavIdx
                              const secPath = `/docs/${res.slug}${sec.anchor ? `#${sec.anchor}` : ''}`
                              return (
                                <div
                                  key={`${sec.anchor || sec.heading}-${sIdx}`}
                                  ref={el => { searchItemRefs.current[secKey] = el }}
                                  className={`docs-modal-section-item ${isSecSelected ? 'is-selected' : ''}`}
                                  onClick={() => {
                                    navigate(secPath)
                                    setSearchModalOpen(false)
                                  }}
                                  onMouseEnter={() => {
                                    if (secNavIdx !== undefined) setSelectedSearchIdx(secNavIdx)
                                  }}
                                  role="button"
                                  tabIndex={0}
                                >
                                  <Hash size={13} className="text-theme-accent-light shrink-0 mt-0.5 opacity-80" />
                                  <div className="flex-1 min-w-0">
                                    <div className="docs-modal-section-heading">
                                      <HighlightText text={sec.heading} query={searchQuery} />
                                    </div>
                                    {sec.snippet && (
                                      <div className="docs-modal-section-snippet">
                                        <HighlightText text={sec.snippet} query={searchQuery} />
                                      </div>
                                    )}
                                  </div>
                                  {isSecSelected && (
                                    <CornerDownLeft size={13} className="text-theme-accent-light shrink-0 mt-1" />
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })
                })()
              ) : searchQuery.trim().length >= 2 ? (
                <div className="p-8 text-sm text-theme-text-muted text-center">
                  No matching documents or sections found for "{searchQuery}"
                </div>
              ) : (
                (() => {
                  const navIndexByKey = new Map(navItems.map((item, i) => [item.key, i]))
                  return (
                    <div className="docs-modal-quick-section">
                      {quickDocs.length > 0 && (
                        <div>
                          <div className="docs-modal-quick-label">
                            <Sparkles size={12} />
                            <span>Quick Jump</span>
                          </div>
                          <div className="docs-modal-quick-grid">
                            {quickDocs.map(doc => {
                              const quickKey = `quick:${doc.slug}`
                              const quickIdx = navIndexByKey.get(quickKey)
                              const isSelected = quickIdx !== undefined && selectedSearchIdx === quickIdx
                              return (
                                <button
                                  key={doc.slug}
                                  ref={el => { searchItemRefs.current[quickKey] = el }}
                                  type="button"
                                  className={`docs-modal-quick-card ${isSelected ? 'is-selected' : ''}`}
                                  onClick={() => {
                                    navigate(`/docs/${doc.slug}`)
                                    setSearchModalOpen(false)
                                  }}
                                  onMouseEnter={() => {
                                    if (quickIdx !== undefined) setSelectedSearchIdx(quickIdx)
                                  }}
                                >
                                  <div className="docs-modal-result-icon">
                                    <BookOpen size={15} />
                                  </div>
                                  <div className="min-w-0 flex-1">
                                    <div className="text-sm font-semibold text-theme-text truncate">{doc.title}</div>
                                    <div className="text-xs text-theme-text-muted truncate mt-0.5">{doc.category_title}</div>
                                  </div>
                                  {isSelected && (
                                    <CornerDownLeft size={13} className="text-theme-accent-light shrink-0 mt-1" />
                                  )}
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {catalog.length > 0 && (
                        <div>
                          <div className="docs-modal-quick-label">
                            <FolderOpen size={12} />
                            <span>Browse Categories</span>
                          </div>
                          <div className="docs-modal-category-chips">
                            {catalog.map(cat => {
                              const firstSlug = cat.documents?.[0]?.slug
                              const catKey = `cat:${cat.id}`
                              const catIdx = navIndexByKey.get(catKey)
                              const isSelected = catIdx !== undefined && selectedSearchIdx === catIdx
                              return (
                                <button
                                  key={cat.id}
                                  ref={el => { searchItemRefs.current[catKey] = el }}
                                  type="button"
                                  className={`docs-modal-category-chip ${isSelected ? 'is-selected' : ''}`}
                                  onClick={() => {
                                    if (firstSlug) navigate(`/docs/${firstSlug}`)
                                    setSearchModalOpen(false)
                                  }}
                                  onMouseEnter={() => {
                                    if (catIdx !== undefined) setSelectedSearchIdx(catIdx)
                                  }}
                                >
                                  <span>{cat.title}</span>
                                  <span className="text-[11px] text-theme-text-muted">({cat.documents?.length || 0})</span>
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })()
              )}
            </div>

            <div className="docs-modal-footer">
              <div className="docs-modal-hints">
                <span><kbd className="docs-modal-hint-kbd">↑↓</kbd> Navigate</span>
                <span><kbd className="docs-modal-hint-kbd">↵</kbd> Open</span>
                <span><kbd className="docs-modal-hint-kbd">Esc</kbd> Close</span>
              </div>
              <span>
                {searchQuery.trim().length >= 2 && !searching
                  ? `${filteredSearchResults.length} doc${filteredSearchResults.length === 1 ? '' : 's'} matched`
                  : 'Search titles & in-file sections'}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
