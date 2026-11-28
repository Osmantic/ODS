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
    'extensions/services/open-webui/readme.md': 'extensions/open-webui',
    'extensions/services/open-webui/readme': 'extensions/open-webui',
  },
  total_documents: 4,
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

const mockDocWsl2 = {
  slug: 'platform/wsl2-gpu',
  title: 'WSL2 GPU Passthrough Setup',
  description: 'Configuring GPU in WSL2',
  category: 'platform',
  category_title: 'Platform & Hardware',
  path: 'docs/WSL2-GPU-PASSTHROUGH.md',
  content: '# WSL2 GPU\n\n## Driver Prerequisites\n\nInstall NVIDIA Windows driver with WSL2 GPU support enabled.\n',
  headings: [
    { level: 1, text: 'WSL2 GPU', id: 'wsl2-gpu' },
    { level: 2, text: 'Driver Prerequisites', id: 'driver-prerequisites' },
  ],
  word_count: 16,
  reading_time_minutes: 1,
  prev: null,
  next: null,
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
      'GET /api/docs/document?slug=platform%2Fwsl2-gpu': mockDocWsl2,
      'GET /api/docs/document?slug=platform/wsl2-gpu': mockDocWsl2,
      'GET /api/docs/search?q=gpu': {
        query: 'gpu',
        total: 1,
        results: [
          {
            slug: 'platform/wsl2-gpu',
            title: 'WSL2 GPU Passthrough Setup',
            description: 'Configuring GPU in WSL2',
            category: 'platform',
            category_title: 'Platform & Hardware',
            match_type: 'title',
            sections: [
              {
                heading: 'Driver Prerequisites',
                anchor: 'driver-prerequisites',
                snippet: 'Install NVIDIA Windows driver with WSL2 GPU support enabled.',
              },
            ],
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

  it('opens search modal on Ctrl+K and displays search results and in-file sections', async () => {
    render(<Docs />)

    await waitFor(() => {
      expect(screen.getAllByText('ODS Overview & Features').length).toBeGreaterThanOrEqual(1)
    })

    // Press Ctrl+K
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    const searchInput = screen.getByPlaceholderText(/Search documentation/i)
    expect(searchInput).toBeInTheDocument()
    // Quick Jump is shown before typing
    expect(screen.getByText(/Quick Jump/i)).toBeInTheDocument()

    // Type query
    act(() => {
      fireEvent.change(searchInput, { target: { value: 'gpu' } })
    })

    await waitFor(() => {
      const dialog = screen.getByRole('dialog', { name: /Search documentation/i })
      expect(dialog).toHaveTextContent('WSL2 GPU Passthrough Setup')
      expect(dialog).toHaveTextContent('Driver Prerequisites')
      expect(dialog).toHaveTextContent('Install NVIDIA Windows driver')
    })

    // Clicking a matched in-file section navigates to #anchor and closes modal
    fireEvent.click(screen.getByText('Driver Prerequisites'))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /Search documentation/i })).not.toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 1, name: /WSL2 GPU Passthrough Setup/i })).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: /Driver Prerequisites/i })).toHaveAttribute('id', 'driver-prerequisites')
    })
  })

  it('supports arrow navigation and Enter in Quick Jump modal', async () => {
    render(<Docs />)

    await waitFor(() => {
      expect(screen.getAllByText('ODS Overview & Features').length).toBeGreaterThanOrEqual(1)
    })

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(screen.getByRole('dialog', { name: /Search documentation/i })).toBeInTheDocument()

    // Move selection to 2nd Quick Jump item ("Quickstart Guide") and open with Enter
    fireEvent.keyDown(window, { key: 'ArrowDown' })
    const dialog = screen.getByRole('dialog', { name: /Search documentation/i })
    const selectedQuick = dialog.querySelector('.docs-modal-quick-card.is-selected')
    expect(selectedQuick).toHaveTextContent('Quickstart Guide')

    fireEvent.keyDown(window, { key: 'Enter' })
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /Search documentation/i })).not.toBeInTheDocument()
    })
  })

  it('assigns unique deduplicated IDs to repeated headings and ignores fenced-code comments', async () => {
    routes['GET /api/docs/document?slug=getting-started%2Foverview'] = {
      ...mockDocOverview,
      content: [
        '# Mode Switch',
        '',
        '```bash',
        '# bash comment inside fence',
        '## Not a heading',
        'ods mode cloud',
        '```',
        '',
        '## Modes',
        '',
        '### Cloud Mode',
        '',
        'First cloud mode section.',
        '',
        '## Architecture',
        '',
        '### Cloud Mode',
        '',
        'Second cloud mode section.',
      ].join('\n'),
      headings: [
        { level: 1, text: 'Mode Switch', id: 'mode-switch' },
        { level: 2, text: 'Modes', id: 'modes' },
        { level: 3, text: 'Cloud Mode', id: 'cloud-mode' },
        { level: 2, text: 'Architecture', id: 'architecture' },
        { level: 3, text: 'Cloud Mode', id: 'cloud-mode-1' },
      ],
    }

    render(<Docs />)

    await waitFor(() => {
      expect(document.getElementById('cloud-mode')).toBeInTheDocument()
    })

    const cloudHeadings = screen.getAllByRole('heading', { level: 3, name: /Cloud Mode/i })
    expect(cloudHeadings).toHaveLength(2)
    expect(cloudHeadings[0]).toHaveAttribute('id', 'cloud-mode')
    expect(cloudHeadings[1]).toHaveAttribute('id', 'cloud-mode-1')

    // TOC sidebar links must target each distinct rendered heading element
    const toc = screen.getByRole('complementary', { name: 'Table of Contents' })
    const tocLinks = Array.from(toc.querySelectorAll('a'))
    expect(tocLinks.map(a => a.getAttribute('href'))).toContain('#cloud-mode')
    expect(tocLinks.map(a => a.getAttribute('href'))).toContain('#cloud-mode-1')

    // Comments inside fenced bash code block must not become heading elements
    expect(screen.queryByRole('heading', { name: /bash comment inside fence/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Not a heading/i })).not.toBeInTheDocument()
  })

  it('resolves nested README links against currentDoc.path and links unregistered files to GitHub source', async () => {
    routes['GET /api/docs/document?slug=getting-started%2Foverview'] = {
      ...mockDocOverview,
      slug: 'getting-started/faq',
      path: 'FAQ.md',
      content: [
        '# FAQ',
        '',
        '- [Open WebUI Settings](extensions/services/open-webui/README.md#settings-come-from-ods)',
        '- [Pixel Notices](vendor/pixel/THIRD_PARTY_NOTICES.md)',
        '- [Schema README](extensions/schema/README.md)',
        '- [Monorepo Root](../README.md#hardware-auto-detection)',
      ].join('\n'),
      headings: [{ level: 1, text: 'FAQ', id: 'faq' }],
    }
    routes['GET /api/docs/document?slug=platform%2Fwsl2-gpu'] = {
      ...mockDocWsl2,
      path: 'docs/MULTI-USER-SETUP.md',
      content: [
        '# Multi-User Setup',
        '',
        'See [Open WebUI](../extensions/services/open-webui/README.md#settings-come-from-ods) and [Pixel Notices](../vendor/pixel/THIRD_PARTY_NOTICES.md).',
      ].join('\n'),
      headings: [{ level: 1, text: 'Multi-User Setup', id: 'multi-user-setup' }],
    }

    render(<Docs />)

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Open WebUI Settings' })).toBeInTheDocument()
    })

    // Open WebUI README resolves to extensions/open-webui, NOT getting-started/overview (root README.md)
    expect(screen.getByRole('link', { name: 'Open WebUI Settings' })).toHaveAttribute(
      'href',
      '/docs/extensions/open-webui#settings-come-from-ods'
    )
    // Unregistered files and nested READMEs fall back to GitHub source URLs, never broken /docs/* routes or root README
    expect(screen.getByRole('link', { name: 'Pixel Notices' })).toHaveAttribute(
      'href',
      'https://github.com/Osmantic/ODS/blob/main/ods/vendor/pixel/THIRD_PARTY_NOTICES.md'
    )
    expect(screen.getByRole('link', { name: 'Schema README' })).toHaveAttribute(
      'href',
      'https://github.com/Osmantic/ODS/blob/main/ods/extensions/schema/README.md'
    )
    expect(screen.getByRole('link', { name: 'Monorepo Root' })).toHaveAttribute(
      'href',
      'https://github.com/Osmantic/ODS/blob/main/README.md#hardware-auto-detection'
    )

    // Navigate to nested doc (docs/MULTI-USER-SETUP.md) and confirm ../ resolution against currentDoc.path
    fireEvent.click(screen.getByText('WSL2 GPU Passthrough Setup'))
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Open WebUI' })).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'Open WebUI' })).toHaveAttribute(
      'href',
      '/docs/extensions/open-webui#settings-come-from-ods'
    )
    expect(screen.getByRole('link', { name: 'Pixel Notices' })).toHaveAttribute(
      'href',
      'https://github.com/Osmantic/ODS/blob/main/ods/vendor/pixel/THIRD_PARTY_NOTICES.md'
    )
  })
})
