# ADR: Assistant First Typed Configuration Contract

**Status:** Accepted for source integration; no secret storage or runtime
activation in this phase

## Decision

Manifest v2 configuration validation is structured data, not an executable
regular expression or a prose string. The bounded v1 vocabulary is:

- `minLength` and `maxLength` for strings and URLs;
- `minimum` and `maximum` for integers; and
- a required, non-empty `choices` list for enums.

Boolean fields do not accept a validation object. Bounds are strict JSON
integers, exclude booleans, use the interoperable safe-integer range, and must
be ordered. Enum choices are unique, bounded strings. URL fields accept only
absolute HTTP or HTTPS URLs with a hostname and reject credentials, fragments,
raw whitespace, backslashes, invalid ports, and control characters.

The typed-configuration module accepts a stored plan envelope plus the expected
hash from the durable transaction record, verifies both that external binding
and the canonical plan bytes, and derives fields only from
`plan.selectedServices`. Those services and `plan.definitions` must be an exact
one-to-one set. A caller cannot supply a smaller service list to omit required
configuration. Shared keys must have byte-equivalent normalized contracts.

The public schema retains every field needed to render and validate a form:
key, type, required status, secret status, source, restart behavior, and
structured validation. Non-secret defaults are also public. Secret defaults
are forbidden. The schema hash covers the complete normalized contract,
including secret-field metadata, but never a submitted value.

Submissions are split into non-secret values and secret values. Keys must be
declared, appear in the correct side, and have `source: user`; both sides receive
the same type and bounds validation. The validation receipt contains only plan
and schema hashes, present key names, and applied non-secret default names.

## Security boundary

This module does not store, return, wrap, serialize, or claim custody over a
secret value. Its exceptions include stable codes and public field identifiers
only. Phase 4B must fetch the envelope from the durable transaction store rather
than accept a caller-supplied envelope, validate and persist secrets inside a
host-owned process, return opaque references and presence state, and revalidate
the exact schema and plan hashes immediately before apply.

No plan, lockfile, chat transcript, model context, API response, browser
persistence, log, diagnostic, or receipt may contain a secret value. The
transaction feature remains disabled until that host-owned composition and the
actual lifecycle adapters are qualified.
