# Kiwix — offline knowledge archive

Read and search a local ZIM archive through Kiwix Serve 3.8.2, pinned by image digest. This optional extension needs no GPU, external search service or database. It serves the archive through a read-only bind mount as UID 1001. The recipe starts `kiwix-serve` directly under `dumb-init`, preserving filenames containing spaces as a single argument and propagating startup errors.

## Prepare and enable

1. Obtain a ZIM archive you trust, for example from the [Kiwix library](https://library.kiwix.org/). Check its publisher, date, license and available disk space. This recipe does not download or update archives automatically.
2. Create `data/kiwix` in your ODS installation and place the archive there. UID 1001 must be able to traverse the directory and read the file; ordinary `0755` directory / `0644` file permissions suffice on rootful Linux.
3. Set `KIWIX_ZIM_FILE='your archive.zim'` in `.env`. This is the existing filename relative to `data/kiwix`, including its `.zim` suffix. It is required; an empty value fails Compose validation. Set `KIWIX_PORT` to change the default 8105 published port.
4. Copy this directory into `extensions/services/kiwix/` and run `./ods enable kiwix`. Open `http://localhost:8105`, select the book, then browse or search it.

The server has no authentication. Keep the default loopback bind or put trusted access controls in your existing reverse proxy before sharing it. Archive contents may include scripts, links and third-party resources; the recipe does not turn arbitrary web archives into a browser sandbox. Offline reading depends on what the archive actually contains. Full-text search requires an index in that ZIM.

## Updates, state and rollback

Stop/disable Kiwix before replacing an archive. Keep the previous file, copy the new archive under a new filename, change `KIWIX_ZIM_FILE`, then enable/recreate the service. Verify the displayed title/date, a known article and a known search result. Revert to the previous filename and recreate to roll back. The container does not modify archives or preserve reading positions on the server; browser state and your ZIM files need their own backups.

This recipe intentionally serves one explicit archive. Multi-book XML libraries and automatic downloads are separate operator configurations. The 512 MiB memory limit is a starting point for small reference books; large archives and concurrent full-text searches need host-specific sizing. A healthy catalog endpoint proves the server started, not that every article or search index is intact.

## Validation

`ODS_TEST_KIWIX_LIVE=1 python -m pytest -q tests/test_kiwix_extension.py` requires Docker Compose, pytest, PyYAML, httpx and `libzim==3.13.0`. It creates a small indexed ZIM locally, uses a filename containing spaces, then checks the real OPDS catalog, Unicode article, full-text search and missing-article 404 before and after container recreation. It also checks that the archive bytes remain unchanged and an unset required filename fails early. The dedicated CI job runs this on Linux amd64. Large public archives, ARM, native Windows/macOS bind permissions and browser rendering of arbitrary archived sites remain unverified.

Kiwix 3.8.2 derives the HTTP book identifier from the filename, including converting spaces to underscores; the ZIM metadata `Name` is a separate value. See the [versioned server API](https://github.com/kiwix/kiwix-tools/blob/3.8.2/docs/kiwix-serve.rst).
