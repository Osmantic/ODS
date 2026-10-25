# Manual resume dependency candidate

This directory builds a derivative of the exact ODS-pinned Hermes release. It
adds `session.resume.manual` and `session.create.manual` (`manual-v1`) without changing auth, plugin
discovery, the upstream entrypoint or the upstream LICENSE. The four source
hashes must match before and after patching; there is no runtime source rewrite.
Generated TypeScript and OpenRPC contracts are retained in the image.

Build locally with `docker build --pull=false --network=none -t
ods-hermes:talk8137-local .` from this directory. This tag is local only. Record
the resulting image ID and the embedded `/opt/ods-hermes-manual-resume/build-manifest.json`.
Do not put this unpublished tag into the production default. Promotion requires
review, published multi-platform digests, and updating `config/hermes-release.json`
and the compose pin together. The original default remains unchanged meanwhile.

An operator-selected image must implement the authenticated method and return
the exact contract. A missing method or invalid response means durable continuity
is unavailable. Transport authentication compatibility does not authorize a
legacy resume or session-create fallback for an existing conversation.

Manual create accepts an opaque 64-hex request key and an optional existing
profile. Its native root is deterministically scoped by authenticated runtime
owner, canonical profile home and request key. It persists an empty DB row before
success and before agent build. Retrying after a lost response resumes that same
root; it never submits a message. Missing authentication/profile, invalid key or
failed persistence refuses the operation. Ordinary upstream create is unchanged.

The ODS integration is opt-in through `ODS_TALK_DURABLE=true`. Its status checks
both actual methods. The published default image remains unsupported for this
opt-in mode until a reviewed derivative or upstream release is promoted. ODS
persists only hashed owner cookie/tab/handle plus native root/tip/runtime and
delivery state under its existing data directory. It supports one API worker;
an OS lock explicitly refuses another worker on the same data directory. Browser
Web Locks prevent a duplicated tab's copied sessionStorage from sharing an active
conversation identity. Images are refused in durable mode until native image
history is implemented. Text attachments and transcribed audio use the same
conversation resolver as text, approval and cancellation.

Ownership is bound to the verified ODS session cookie, not a stable account ID.
Expiry requires owner authentication again; a newly issued cookie cannot reopen
the retained handle. This slice does not silently rebind it. Web Locks requires
a supporting browser and a secure context (or localhost); unsupported contexts
report unavailable rather than sharing a copied tab identity.

This candidate does not qualify image history, multi-process Hermes writers,
distributed leases, turn-generation fencing, active-stream replay after reload,
ARM64, browser rendering, actual model/tool execution or fleet acceptance.
