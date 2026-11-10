import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Docs from './Docs'

const mockCatalog = {
  categories: [
    {
      id: 'getting-started',
      title: 'Getting Started',
      description: 'Onboarding and guides',
      order: 1,
      documents: [
        {
          slug: 'getting-started/overview',
          category: 'getting-started',
          title: 'ODS Overview & Features',
          description: 'Overview of ODS architecture',
          order: 1,
        },
        {
          slug: 'getting-started/quickstart',
          category: 'getting-started',
          title: 'Quickstart Guide',
          description: 'Fast-path install',
          order: 2,
        },
      ],
    },
    {
      id: 'platform',
      title: 'Platform & Hardware',
      description: 'OS setup guides',
      order: 2,
      documents: [
        {
          slug: 'platform/wsl2-gpu',
          category: 'platform',
          title: 'WSL2 GPU Passthrough Setup',
          description: 'Configuring GPU in WSL2',
          order: 1,
        },
      ],
    },
  ],
  path_map: {
    'docs/macos-quickstart.md': 'platform/macos-quickstart',
    'macos-quickstart.md': 'platform/macos-quickstart',
    'quickstart.md': 'getting-started/quickstart',
  },
  total_documents: 3,
}

const mockDocOverview = {
  slug: 'getting-started/overview',
  title: 'ODS Overview & Features',
  description: 'Overview of ODS architecture',
  category: 'getting-started',
  category_title: 'Getting Started',
  path: 'README.md',
  content: '# ODS Overview\n\nWelcome to ODS. See [macOS Guide](docs/MACOS-QUICKSTART.md) and [Quickstart](QUICKSTART.md).\n\n> [!NOTE]\n> This is an important system note.\n\n```python\nprint("Hello ODS")\n```\n',
  headings: [
    { level: 1, text: 'ODS Overview', id: 'ods-overview' },
  ],
  word_count: 24,
  reading_time_minutes: 1,
  prev: null,
  next: {
    slug: 'getting-started/quickstart',
    title: 'Quickstart Guide',
  },
}

function response(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  })
}

describe('Docs Component', () => {
  let routes

  beforeEach(() => {
    routes = {
      'GET /api/docs/catalog': mockCatalog,
      'GET /api/docs/document?slug=getting-started%2Foverview': mockDocOverview,
      'GET /api/docs/document?slug=getting-started/overview': mockDocOverview,
      'GET /api/docs/search?q=gpu': {
        query: 'gpu',
        results: [
          {
            slug: 'platform/wsl2-gpu',
            title: 'WSL2 GPU Passthrough Setup',
            description: 'Configuring GPU in WSL2',
            category: 'platform',
            category_title: 'Platform & Hardware',
          },
        ],
      },
    }

    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const key = `${options.method || 'GET'} ${url}`
      const handler = routes[key]
      if (!handler) return response(404, { detail: `unexpected ${key}` })
      return response(200, typeof handler === 'function' ? handler() : handler)
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders documentation layout, title, and markdown content', async () => {
    render(<Docs />)

    // Wait for document to load
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: /ODS Overview & Features/i })).toBeInTheDocument()
    })

    // Verify categories and document links in sidebar
    expect(screen.getAllByText('Getting Started').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Platform & Hardware')).toBeInTheDocument()
    expect(screen.getAllByText('Quickstart Guide').length).toBeGreaterThanOrEqual(1)

    // Verify reading time
    expect(screen.getByText(/1 min read/i)).toBeInTheDocument()

    // Verify markdown internal links are rewritten to docs slugs instead of relative 404 paths
    const macosLink = screen.getByRole('link', { name: 'macOS Guide' })
    expect(macosLink).toHaveAttribute('href', '/docs/platform/macos-quickstart')

    const quickstartLink = screen.getByRole('link', { name: 'Quickstart' })
    expect(quickstartLink).toHaveAttribute('href', '/docs/getting-started/quickstart')
  })

  it('filters documents when searching in navigation sidebar', async () => {
    render(<Docs />)

    await waitFor(() => {
      expect(screen.getAllByText('ODS Overview & Features').length).toBeGreaterThanOrEqual(1)
    })

    const filterInput = screen.getByPlaceholderText('Filter documentation...')
    fireEvent.change(filterInput, { target: { value: 'WSL2' } })

    const navSidebar = document.querySelector('.docs-nav-sidebar')
    // Non-matching docs should be hidden in navigation sidebar
    expect(navSidebar.textContent).not.toContain('ODS Overview & Features')
    // Matching doc should be visible in navigation sidebar
    expect(navSidebar.textContent).toContain('WSL2 GPU Passthrough Setup')
  })

  it('opens search modal on Ctrl+K and displays search results', async () => {
    render(<Docs />)

    await waitFor(() => {
      expect(screen.getAllByText('ODS Overview & Features').length).toBeGreaterThanOrEqual(1)
    })

    // Press Ctrl+K
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    const searchInput = screen.getByPlaceholderText(/Search documentation/i)
    expect(searchInput).toBeInTheDocument()

    // Type query
    act(() => {
      fireEvent.change(searchInput, { target: { value: 'gpu' } })
    })

    await waitFor(() => {
      expect(screen.getByText('WSL2 GPU Passthrough Setup')).toBeInTheDocument()
    })
  })
})
