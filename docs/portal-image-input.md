# Portal image input (draft implementation)

This candidate adds image attachments to the Portal composer. It is not yet
qualified for deployment or merge. A model must support image input to interpret
the attachment; accepting a file is not evidence of visual understanding.

## Behavior

- Select, paste or drop still PNG, JPEG or WebP images. Send an image-only turn
  or include text. Original bytes are preserved, including embedded metadata.
- Up to four images and 8 MiB of combined image bytes per turn; at most 8192
  pixels on either axis and 16 million pixels per image. Animation, malformed
  content and mismatched file formats are rejected before storage.
- A verified text-only route refuses image submission. Unknown capability
  requires explicit consent for that conversation and exact route. Consent does
  not establish support, and switching the route requires a fresh decision.
- Uploads stay private behind the Dashboard authentication boundary, scoped to
  its owner namespace and conversation. This uses the existing installation
  owner boundary, not a new per-person ACL for shared Dashboard administrators.
- Chat history contains immutable image references, not base64 data. The native
  image-read tool can retrieve admitted bytes after transcript image pruning.
  It rechecks session identity and model route before returning typed images.
- Removing an unsubmitted attachment discards its API upload only after server
  confirmation. Images retained by a send attempt are protected from draft
  removal. Failed dispatch can conservatively retain an unused image.
- Unsent uploads unused for seven days can be reclaimed during later uploads.
  Reading the image or uploading the same bytes again renews that draft lease.
  Images retained by a send attempt never expire through this cleanup.
- JSON conversation export is not an image backup. Import into another chat
  does not transfer access to private image bytes. Attach the images again.
- Deleting a conversation waits for authenticated confirmation before removing
  its browser history. The API and native runtime retain deletion records so
  stale uploads and conversation replay cannot restore its images. Failed
  deletion keeps the conversation visible and can be retried.
- Deletion removes private image copies and registered native session
  transcripts, including archives created by the pinned runtime. It preserves
  workspace files and published previews. Unregistered legacy transcripts,
  external backups, exports and provider retention are outside this cleanup.

## Resource boundaries

The API store is limited to 128 MiB of image data and 512 images. The native
private cache has a separate 128 MiB physical quota. Neither evicts existing
conversation images automatically. Upload/decode and Edge image admission are
bounded; overload is retryable instead of queuing unlimited image bodies.

Marked, validated image turns can use a 16 MiB internal Edge envelope for
base64 encoding; normal requests retain their previous limit. Compressed chat
request bodies are refused, preventing automatic decompression before the
bounded reader. Browser-to-API chat still carries references, not image bytes.

The Edge container limit is 192 MiB (previously 128 MiB); its 32 MiB reservation
is unchanged. A repeated maximum-image/near-maximum-history fixture with Unicode
peaked near 125 MiB after copy reductions, leaving insufficient margin under the
old limit. This is a ceiling, not a promise of permanent RAM use. One image turn
is admitted at a time. The internal image envelope uses ASCII-escaped JSON to
preserve Unicode without widening the whole base64-bearing source string;
structural depth and token counts are bounded before allocating parsed objects.

## Qualification still required

- Real cloud visual recognition and unsupported-provider responses.
- Authenticated browser upload, paste/drop, reload, retry and model switching
  against the combined installed services.
- Conversation deletion across both image stores and recovery from a native
  cache writer interruption. Interrupted uploads use the unsent-draft lease;
  isolated lifecycle and native SDK tests exist, but browser acceptance of
  that recovery remains required.
- Integration with the separate ZIP text-import change and other pending
  Portal updates; clean-install and upgrade acceptance.
- Exact-head CI, including real grammar compilation and the pinned runtime
  image tool-result serialization test.

Unit, component and isolated transport tests do not replace these checks. The
runtime wire integration verifies typed image bytes reach the provider message
serializer; it does not contact a model or establish visual comprehension.
