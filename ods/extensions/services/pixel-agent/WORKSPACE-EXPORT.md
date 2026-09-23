# Exact workspace export plans

`pixel_ods_workspace_export_plan` prepares a bounded ordinary `exec` call. It does
not access files, run source code, execute tests, publish a preview, or establish
that a task is complete. Its receipt always says `executed: false`.

Select each existing source and each new output explicitly. For example:

```json
{
  "files": [
    {"source": "src/app.ts", "destination": "deliveries/v2/app.ts.txt", "key": "app.ts"},
    {"source": "src/utils.ts", "destination": "deliveries/v2/utils.ts.txt", "key": "utils.ts"}
  ],
  "textMap": "deliveries/v2/source-text.json"
}
```

Run the returned `exec` argument object with the ordinary tool from its configured
default workspace root. That separate call remains subject to ordinary execution
permissions, sandboxing, cancellation and tool hooks. Python 3 must be available.
Do not translate the command into a host path or execute it through the preview
broker. Destination parent directories must already exist; their creation is a
separate, explicit workspace operation.

The fixed serializer copies raw bytes and, when `textMap` is present, serializes
complete UTF-8 text under the selected keys. Quotes, escapes, CRLF, and final
newlines are preserved in each decoded value. Omit `destination` for map-only
entries; omit `textMap` and all keys for raw copies, including binary files.
Invalid UTF-8 in a requested map fails before any output is created.

Outputs are create-only. For a later export, choose new versioned paths explicitly;
existing files are never overwritten or removed to force an export. Sources must
be regular, non-symlink, single-link files. Relative traversal, symlink parents,
aliased paths, missing parents and output collisions are rejected. The batch is
bounded to 16 files, 1 MiB per source and 4 MiB total, with a smaller request limit
when paths are long. The plan text is limited to 3,500 characters so the full
deferred result fits the minimum runtime cap; the execution receipt is limited
to 2,400 UTF-8 bytes. Requests exceeding either bound fail without an export. Detected failures remove only files created by that invocation;
abrupt process termination is not a transactional rollback guarantee.

Inspect the actual execution status and its readback hashes and byte counts. A
successful export verifies those copies and the optional map only. It does not
verify tests, saved command output, publication or owner requirements. Testing and
publication remain separate actions, and an export after publication requires
fresh publication like any other workspace-changing command.
