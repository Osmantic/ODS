# Pixel Edge fixture remediation evidence

Date: 2026-09-23. This records the local working tree, not a published release.
The source base is `4099cffc874f80b45f589504ebb29d4dbdcbd5c6`; the fixture changes remain uncommitted.

## Cause and correction

The prior full-suite runs reported the same 64 failures with aiohttp 3.11.12
and 3.14.3. The general HTTP fixture reused one value for chat and owner/preview
credentials and inherited the production container's transition-state directory.
That selected an enabled gate with invalid credential separation instead of the
fixture's intended transport-only configuration. The runtime correctly failed
closed with `owner_credential_not_distinct` before the target behavior ran.

The correction is confined to `tests/test_pixel_edge.py`:

- Chat and owner/preview credentials are distinct synthetic values.
- Preview tests authenticate with the preview credential; chat/activity tests
  use the chat credential.
- General HTTP fixtures explicitly select the optional disabled-gate profile
  and restore the surrounding environment after each case. They do not touch
  or initialize a production state directory.
- The dedicated transition-gate suite continues provisioning real private
  temporary state and exercising the enabled gate, persistence, cancellation,
  unsafe-file rejection, and equal-key rejection. Its adversarial equal-key
  test remains unchanged.
- Preview authorization now rejects the chat credential as well as missing
  authentication, without forwarding either request upstream. The activity
  endpoint accepts chat credentials, rejects preview credentials, and adds
  no preview CORS allowance in either case.

No runtime authorization guard or product behavior changed in this follow-up.
Both `pixel_edge.py` and `transition_gate.py` remain unchanged.

## Validation

| Run | Result |
| --- | --- |
| Prior baseline, aiohttp 3.11.12 | 64 failed, 101 passed, 24 subtests passed |
| Prior upgraded runtime, aiohttp 3.14.3 | Identical 64 failed, 101 passed, 24 subtests passed |
| Final corrected fixtures, aiohttp 3.14.3 | **150 passed, 39 subtests passed, zero failures**, 14.73 seconds |
| Changed fixture whitespace | `git diff --check` passed |

The prior failure count includes 15 failing subtest cases. No tests were deleted
or skipped by the correction; all 150 top-level tests and all 39 subtests pass.
The 948 warnings remain visible: aiohttp string application-key warnings in the
mock upstream and existing test application-state mutation deprecations. They
were not suppressed to obtain a pass.

Runtime: Python 3.11.16, aiohttp 3.14.3, Linux container.
Local image: `ods-audit-pixel-edge:20260923`.
Image ID: `sha256:d7c459bfdb210e3cdccded7d53efceb533f3e23a63055b07a88f659d48f550e4`.
Final fixture SHA-256: `7813b9781306b6ce22951d9b38a2484294e6695a506e0cb723cb2fd3376df1dc`.

The repository was mounted read-only. The disposable test container ran as UID 0
and installed pytest after restoring pip with ensurepip; the runtime image itself
was not changed. This validates the Python suite, including real local HTTP and
Unix-socket fixtures, not a live ODS deployment or model/hardware qualification.

Reproduction (replace the checkout placeholder with an absolute local path):

```sh
docker run --rm --user 0 \
  --mount "type=bind,source=<absolute-checkout>,target=/repo,readonly" \
  -w /repo/ods/extensions/services/pixel-edge \
  ods-audit-pixel-edge:20260923 sh -c \
  'python -m ensurepip >/dev/null && python -m pip install --quiet pytest && python -m pytest tests -q -p no:cacheprovider'
```

The final local log is `output/edge-fixture-remediation-pytest.log`.
The pre-correction logs are `output/dependencies-edge-pytest.log` and
`output/dependencies-edge-baseline-pytest.log`. This sanitized receipt omits
personal paths and credential values. The passing suite resolves the Pixel Edge
test blocker; it does not resolve the separately recorded OS-package scanner
findings or imply complete PB-004 release acceptance.
