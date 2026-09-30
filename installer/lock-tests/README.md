# Native installer process lock tests

Run `cargo test --locked --manifest-path installer/lock-tests/Cargo.toml`.

This small crate imports the production `install_lock.rs` directly and needs no
Tauri GUI dependencies. Its separate harmless processes cover contention and
release after return, error, unwind, and forced helper termination on Windows,
Linux, and macOS. They never launch an ODS installation.

The complete Tauri crate additionally tests the command admission guard and
recovery after lock I/O errors. Reclaiming a lock after process death does not
demonstrate containment or cleanup of orphan installer descendants.
