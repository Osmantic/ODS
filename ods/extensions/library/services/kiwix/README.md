# Kiwix — offline ZIM library

Kiwix Tools 3.8.2, GPL-3.0-or-later, official multiarchitecture image pinned by digest. Upstream: https://github.com/kiwix/kiwix-tools/tree/3.8.2.

Read/search explicitly imported ZIM archives through the native Kiwix web interface at `http://localhost:11099/`. This is an offline reference library, not an AI model or a general website crawler. Search depends on indexes contained in each archive. The initial library is empty; no encyclopedia or other large dataset is downloaded automatically.

## Import real content

Choose/download a ZIM archive yourself, with enough free storage for it, and retain the archive's own license/attribution. Kiwix's code license does not replace the content license. For example, once this extension has been installed:

```text
docker cp "PATH_TO_YOUR_ARCHIVE.zim" ods-kiwix:/data/books/reference.zim
docker exec --user 0 ods-kiwix chmod 644 /data/books/reference.zim
docker restart ods-kiwix
```

Replace the quoted source path with the actual Windows/macOS/Linux path. These commands deliberately import one selected file; the recipe never scans host folders. The permission command only makes this copied archive readable by the nonroot server. Use distinct destination names when adding more books. The ODS-managed index is rebuilt from top-level `/data/books/*.zim` at startup with native `kiwix-manage`, including filenames containing spaces. Invalid archives stop startup rather than being silently omitted. Removing an archive and restarting removes it from the index; stop active readers before replacing/removing content. Multipart archives are not imported by this recipe.

The import instructions are a manual content workflow, not a claim that Portal already has an automatic Kiwix upload tool. Installing Kiwix does not import chat attachments or project files.

## Storage and exposure

`kiwix-data` persists the archives and generated `ods-library.xml` under `/data`; the XML index can be rebuilt from the original archives. Back up the volume before replacing files. The image's UID/GID 1001 owns the initial named volume. The container root filesystem is read-only, with bounded `/tmp`, one GiB memory and two CPUs. Large multi-book searches may require deliberate memory adjustments.

The server has no application login. The published port is loopback-only, but services sharing `ods-network` can also access it: import only content appropriate for that scope. Remote publication requires an explicitly configured authenticated proxy. External navigation is blocked by Kiwix's native `--blockexternal` option; this is not a general guarantee about every active asset in an untrusted archive.

## Platforms and validation

Official image includes amd64, arm64 and other Linux variants; this ODS recipe targets amd64/arm64 through Docker on Windows/Linux/macOS. No GPU, loaded model or cloud account is required. Native empty-library behavior was checked in upstream source. HTTP health only checks the library page, not archive integrity or search results. Image build, real archive import/search and platform runtime validation remain pending; no service/model was started during preparation.
