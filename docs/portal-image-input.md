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
- JSON conversation export is not an image backup. Import into another chat
  does not transfer access to private image bytes. Attach the images again.

## Resource boundaries

The API store is limited to 128 MiB of image data and 512 images. The native
private cache has a separate 128 MiB physical quota. Neither evicts existing
conversation images automatically. Upload/decode and Edge image admission are
bounded; overload is retryable instead of queuing unlimited image bodies.

Marked, validated image turns can use a 16 MiB internal Edge envelope for
base64 encoding; normal requests retain their previous limit. Compressed chat
request bodies are refused, preventing automatic decompression before the
bounded reader. Browser-to-API chat still carries references, not image bytes.

## Qualification still required

- Real cloud visual recognition and unsupported-provider responses.
- Authenticated browser upload, paste/drop, reload, retry and model switching
  against the combined installed services.
- Conversation deletion across both image stores, abandoned in-flight upload
  cleanup and recovery from a native cache writer interruption.
- Integration with the separate ZIP text-import change and other pending
  Portal updates; clean-install and upgrade acceptance.
- Exact-head CI, including real grammar compilation and the pinned runtime
  image tool-result serialization test.

Unit, component and isolated transport tests do not replace these checks. The
runtime wire integration verifies typed image bytes reach the provider message
serializer; it does not contact a model or establish visual comprehension.
