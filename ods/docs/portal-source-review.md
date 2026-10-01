# Review project source and published output

For a built project, publish the runnable output and capture the source separately:

```json
{"relativeDirectory":"Playground/financas/dist","sourceDirectory":"Playground/financas"}
```

`pixel_ods_workspace_preview` accepts the optional `sourceDirectory` only when it
is the publication directory or an ancestor of it inside the configured owner
workspace. It does not build, execute, or copy source into the website. A requested
source capture that fails causes the tool to fail; it never silently reports a
complete source delivery. Existing calls without this field retain their behavior.

Review has three distinct views:

- **Source files:** a separately hashed, immutable capture of eligible UTF-8 text,
  with original paths and complete file contents. Captured source is not proof
  that these bytes produced the published build.
- **Published output:** the existing snapshot comparison, including compiled
  assets. Preview continues to render this output.
- **File edits:** existing, filtered tool-reported excerpts from the response.
  These are not full source files or filesystem verification.

Old publications remain readable and say when no source snapshot was captured.
Republishing with `sourceDirectory` captures source for the new receipt. Two
captures can share an unchanged website snapshot but have distinct source IDs;
each stored receipt retains its own source identity. As before, opening a project
from a conversation selects its latest publication; this does not add a historical
source-version picker.

Capture allows at most 128 text files, 256 KiB per file and 1 MiB total, with a
4 MiB encoded receipt document and bounded directory enumeration. Exceeding a
quota fails explicitly instead of returning a silently truncated inventory.
Hidden entries, dependencies, generated output, known sensitive filenames and
files matching the sensitive-content filter are excluded and counted. The UI
labels the inventory as eligible text, not the whole project. These filters are
not a general-purpose secret scanner: source code should not contain credentials.
Symlinks, hardlinks, special files, unsafe ownership/modes, and changes detected
during capture are rejected. File reads use directory descriptors and no-follow
opens. No host path, package download, or execution permission is added.

Sources are stored separately from the site under a private immutable sidecar.
The public localhost preview listener returns 404 for source metadata. Only the
existing authenticated Dashboard/Pixel Edge/private Unix relay can serve the
closed source endpoint. It has no preview CORS permission and uses a non-executable
JSON response. The browser verifies the document hash, project/site binding,
counts, limits, and each file's original UTF-8 byte hash before displaying it.
Changing conversations or source revisions aborts in-flight source reads.

Source storage is bounded to 128 captures and 64 MiB per owner store, serialized
under a filesystem lock before creating a new website output snapshot. A failed
output publication releases its temporary source reservation. This is not a global
transaction against disk failure after publishing output. Repeating an existing capture is idempotent. At capacity,
a new capture fails explicitly and previous revisions remain readable; no automatic
pruning or retention promise is introduced. Interrupted temporary files count
toward capacity and are not removed by guessing filenames.

Authorization follows the existing owner-administrator Dashboard boundary: a
trusted local browser or authenticated remote administrator passes the nginx gate,
and only the server-held proxy credential can access Edge. Source hashes are
integrity identifiers, not access credentials. This is not per-conversation or
multi-user source isolation. Publication receipts originate from the existing
run-scoped guard and SSE verification; reading an accepted snapshot remains an
administrator operation across that owner's workspace.
