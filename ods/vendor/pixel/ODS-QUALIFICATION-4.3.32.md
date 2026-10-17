# ODS-maintained Pixel 4.3.32 candidate

Functional source checkpoint: `b6edcbb37548f18fd34a8f7589d4f77006849eca` in
the public ODS repository. This is an ODS-maintained candidate, not an upstream
Pixel release or a completed installation qualification.

## Change

Portal's agent sandbox had bash, python3, git, rg and jq but no JavaScript
runtime. Building a browser game on the Arm64 RTX Spark, the model could not
run, test or syntax-check its code: it tried `node` four times, built stand-in
harnesses twice, and runtime bugs (canvas state leaks, a crash on a missing
field, a per-hit freeze) survived many turns.

The sandbox image now installs the official Node.js 24.21.0 `node` binary.
`dpkg --print-architecture` selects the amd64 or arm64 build, pinned by URL and
SHA-256 in `RELEASE-MANIFEST.json` (`sandboxNodeRuntime`) and generated into
`deploy/sandbox/Dockerfile`. Any other architecture stops the build. Only
`bin/node` and `LICENSE` are extracted, so npm, npx and corepack are absent,
and the build fails if one is reachable. The pins come from the release's
`SHASUMS256.txt`, whose clearsigned `SHASUMS256.txt.asc` verified with Node.js
release key `5BE8A3F6C8A5C01D106C0AD820B1A390B168D356` (listed in the Node.js
README). Plain `RUN` syntax keeps legacy Docker builders working.

`pixel bootstrap` runs a new or reused candidate's node once with no network, a
read-only root and no capabilities. It requires the pinned version for the host
architecture and no package manager before any live change. Native macOS
preparation runs the same check in its linux/arm64 image.

The runtime confinement is unchanged: network none, read-only root, the same
tmpfs, memory, process and file limits, and the same single workspace bind.
Node adds no capability that bash and python3 do not already have. Version
metadata advances from 4.3.31 to 4.3.32 so bootstrap builds a new candidate and
the strict source-upgrade gate accepts the changed source. OpenClaw, plugin and
dependency versions, the base image digest, policies and trust anchors are
unchanged.

## Current evidence

- `tests/test_pixel_sandbox_node_pins.py` (ODS) checks the manifest, Dockerfile,
  generated files, bootstrap and macOS proofs, and that the agent's prompt
  claims node exactly as the image provides it.
- `tests/e2e.sh` exercises the bootstrap proof against the fake Docker: the
  pinned runtime passes; a wrong version, a wrong architecture, a package
  manager, empty output, a failed run and an unknown image are refused.
- `.github/workflows/pixel-sandbox-image.yml` (ODS) builds this image on amd64
  with BuildKit and the legacy builder and on arm64 natively and with
  `--platform linux/arm64`, runs it with the production confinement, and runs
  node through the real pinned OpenClaw Docker sandbox. Its results belong
  in the ODS pull request.

## Fleet qualification still required

Record on each host after merge: Linux amd64, WSL x64, WSL on the Arm64 RTX
Spark, and a macOS native update; in Sandbox mode and with Full Access.

- `docker exec` into the live `pixel-sbx-agent-pixel-*` container prints
  `node --version` as `v24.21.0` and finds no npm.
- A Portal turn syntax-checks and tests a project's JavaScript with node.
- On macOS, the running sandbox container uses the new image ID after update.
- Rolling back to 4.3.31 restores the preserved image; the agent then reports
  node as missing once instead of probing for it.

The release stays a candidate until that qualification passes. The previous
qualification records remain historical evidence. The outer provenance file
records the reproducible public-only bundle.
