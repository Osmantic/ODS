# Portal harness migration and acceptance contract

This page describes the design and verification boundary for managed extension
installation. It replaces a development diary containing machine-local state.
It does not certify an installed deployment or the completion of a migration.
Use the exact selected ODS commit and its published validation results when
assessing release readiness; see [Release validation](RELEASE_VALIDATION.md).

## Ownership and execution

The model investigates a project, prepares integration files, interprets build
errors, and proposes repairs. ODS owns authentication, execution boundaries,
operation identity, cancellation, persistence, and promotion into managed
Extensions. A proposal or an assistant's success message is not an installation
receipt. Observe an uncertain operation before dispatching another attempt.

Catalog requests reuse an existing recipe. GitHub requests bind a repository,
immutable revision, and recipe to an isolated preparation workspace before
managed promotion. Preparation must not silently install an unmanaged service
on the host. Preserve the owner's selected model, context limits, GPU backend,
existing service configuration, and data throughout that lifecycle.

## Acceptance matrix

| Boundary | Required verification |
| --- | --- |
| Durable continuation | Preserve the authenticated request and immutable recipe across retries, compaction, UI closure, and restart; reject another owner's records. |
| Preparation | Bind workspace and processes to the request; detect actual OS, architecture, resources, network policy, and sandbox support. |
| Promotion | Revalidate the prepared revision and prerequisites before host execution; retain the accepted operation ID across reconnects. |
| Observation | Distinguish proposed, prepared, running, failed, uncertain, and installed states; report unavailable observations explicitly. |
| Cancellation and repair | Prove process termination or preserve uncertainty; do not replay failed or unobserved mutations automatically. |
| Application verification | Exercise actual runtime behavior after installation; import, build, or HTTP health success alone is insufficient. |
| Platform support | Verify Linux, WSL/Windows, and macOS transports separately on the claimed platform; fixture tests are not hardware qualification. |

A useful release exercise includes a catalog service, an uncatalogued library,
a web application, and a project with build dependencies. Include required
configuration, failed build and repair, cancellation, reconnect, and repeated
requests. Publish only sanitized, commit-bound results with explicit skips and
remaining limitations.

## Source and test entry points

- [Request coordination](../extensions/services/dashboard-api/extension_requests.py)
  and [request tests](../extensions/services/dashboard-api/tests/test_extension_requests.py).
- [Installation planning](../extensions/services/dashboard-api/extension_install_plan.py)
  and [planning tests](../extensions/services/dashboard-api/tests/test_extension_install_plan.py).
- [Installation implementation](../extensions/services/dashboard-api/extension_installation.py)
  and [installation tests](../extensions/services/dashboard-api/tests/test_extension_installation.py).
- [Request status tests](../extensions/services/dashboard-api/tests/test_extension_request_status.py).
- [Extension readiness criteria](EXTENSION-READINESS.md).

These links identify review surfaces, not passing results. A release receipt
must name the source SHA, commands, outcomes, artifact digests, platform class,
and unverified behavior. Keep raw transcripts, user paths, session identifiers,
private infrastructure labels, and backup locations outside published docs.
