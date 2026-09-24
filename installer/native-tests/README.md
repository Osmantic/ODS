# Native installer contract tests

This small crate compiles the actual production
`src-tauri/src/installer.rs` and `src-tauri/src/state.rs` modules through path
imports. It runs their Rust tests without linking Tauri or installing WebView
development libraries. The root GitHub Actions workflow runs it on Linux,
Windows and macOS using Rust 1.96.0.

From this directory:

```sh
python check_lock.py
cargo test --locked
```

Python 3.11 or newer is required for the lock check. The harness's registry
dependencies must be a subset of the application's Cargo.lock versions.
When updating native dependencies, update this lockfile too; the parity
check fails instead of silently testing different serializer versions.

These are module and process-boundary tests, not a complete installer build.
The six existing tests exercise checkout/ref validation. Tests added inside
the included modules are picked up automatically. Compiling state.rs checks
its types against the orchestrator but does not by itself test persistence.
This crate does not compile Tauri command macros, GPU/platform probes or the
frontend, and does not prove installation on real hardware. Continue to use the native
application's packaging workflow and platform smoke gates for those claims.

Tests should use local process fixtures and isolated state directories.
A fixture helper marked ignored may be launched explicitly by a parent test
to isolate environment variables; do not run all ignored helpers manually.
The crate is unpublished and adds no runtime dependency to the application.
