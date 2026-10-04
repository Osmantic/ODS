# ODS desktop installer (unsupported)

This Tauri app is **not a supported way to install ODS**. Use the commands in
the [main README](../README.md#get-started) instead.

- No CI workflow builds or tests it. `installer/.github/workflows/build.yml`
  never runs, because GitHub only runs workflows in the repository's top-level
  `.github/workflows/`.
- No release ships its binaries.
- Its installer calls no longer match ODS:
  - It always passes `--tier`, which skips hardware detection.
  - It passes `--image-gen`, which the Linux and macOS installers reject.
  - On Windows it runs `install.ps1 -NonInteractive`, which does not install
    WSL, Docker Desktop or Ubuntu and rejects `-Tier 0`.

Do not distribute builds of this directory. Rebuilding it on the current
installers, with a CI build, is tracked as a future change.
