# Mermaid Live Editor for ODS

Edit Mermaid source and view flowcharts, sequence diagrams, state diagrams and
other supported formats in the browser. This is the MIT upstream editor built
from commit `8754416d847d2947c0492bc045cddbab717a51ea`, not an invented diagram UI.
No stable GitHub release was returned during research; this is a pinned source
snapshot with package version 2.0.67, not a claimed tagged release.

## Use

Install `mermaid-live-editor` and open `http://localhost:11071`.
`MERMAID_LIVE_EDITOR_PORT` changes the loopback port. Enter your diagram source
and use the editor's local rendering/export controls. Any upstream starter
diagram is an editable example, not an analysis of your project. No Portal
conversation or workspace is imported automatically.

History/preferences belong to the browser origin. Save/download your diagram
source before clearing browser storage. Switching browser profiles or ports can
separate that state. There is no account database or server document volume.
Do not mistake an encoded diagram URL for private server storage: anyone with
such a link may recover its diagram source.

## Local build choices

Upstream embeds configuration at build time. The recipe disables mermaid.ink and
Kroki action links, analytics and Mermaid Chart commercial links during that
build. Changing those environment variables only at container startup will not
change the compiled editor. External-renderer export links are consequently not
available; this does not disable browser diagram rendering. No paid account or
external rendering service is required for the local editor.

The source archive is checksum-verified, pnpm uses the checked-in lockfile and
packageManager pin, and the generated static files are served by pinned nginx
under UID 101 with a read-only filesystem. Build tools stay outside the final
runtime image. Building requires network access for the source and dependencies.

Intended platforms are amd64/arm64 Linux containers under Docker Engine on Linux
or Docker Desktop on Windows/macOS. There is no GPU or language-model dependency.
HTTP readiness does not prove browser render/export functionality. Dependency
installation/build, diagram rendering, exports, history and platform behavior
remain runtime-pending. No application containers were started.

Source: https://github.com/mermaid-js/mermaid-live-editor
