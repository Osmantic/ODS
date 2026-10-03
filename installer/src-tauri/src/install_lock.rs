use fs2::FileExt;
use std::fs::{File, OpenOptions};
use std::path::Path;

/// All native installer windows share one state file, even for different checkouts.
/// Keep this file in place: unlinking it could create independently locked inodes.
pub fn acquire(path: &Path) -> Result<File, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|err| format!("Cannot prepare the installer lock directory: {err}"))?;
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)
        .map_err(|err| format!("Cannot open the installer lock: {err}"))?;
    file.try_lock_exclusive().map_err(|err| {
        if err.raw_os_error() == fs2::lock_contended_error().raw_os_error() {
            "An ODS installation is already in progress.".to_string()
        } else {
            format!("Cannot acquire the installer lock: {err}")
        }
    })?;
    Ok(file)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Child, Command, Stdio};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "ods-process-lock-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            std::fs::create_dir_all(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    struct OwnedChild(Child);
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    fn lock_child() {
        let Some(directory) = std::env::var_os("ODS_LOCK_TEST_DIRECTORY") else {
            return;
        };
        let directory = std::path::PathBuf::from(directory);
        let file = acquire(&directory.join("installer.lock")).unwrap();
        std::fs::write(directory.join("ready"), b"ready").unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        while !directory.join("release").exists() {
            assert!(Instant::now() < deadline, "Parent did not release helper");
            std::thread::sleep(Duration::from_millis(10));
        }
        match std::env::var("ODS_LOCK_TEST_MODE").unwrap().as_str() {
            "unwind" => {
                assert!(std::panic::catch_unwind(move || {
                    let _file = file;
                    panic!("Harmless test worker unwind");
                })
                .is_err());
            }
            "error" => {
                let result = (move || -> Result<(), ()> {
                    let _file = file;
                    Err(())
                })();
                assert!(result.is_err());
            }
            _ => drop(file),
        }
    }

    #[test]
    fn another_process_is_busy_and_releases_on_return_error_unwind_or_death() {
        for mode in ["return", "error", "unwind", "death"] {
            let fixture = Fixture::new();
            let test_module = module_path!().split_once("::").unwrap().1;
            let mut child = OwnedChild(
                Command::new(std::env::current_exe().unwrap())
                    .args([
                        "--exact",
                        &format!("{test_module}::lock_child"),
                        "--nocapture",
                    ])
                    .env("ODS_LOCK_TEST_DIRECTORY", &fixture.0)
                    .env("ODS_LOCK_TEST_MODE", mode)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .unwrap(),
            );
            let deadline = Instant::now() + Duration::from_secs(10);
            while !fixture.0.join("ready").exists() {
                assert!(
                    Instant::now() < deadline,
                    "Helper failed to acquire lock ({mode})"
                );
                assert!(
                    child.0.try_wait().unwrap().is_none(),
                    "Helper exited before acquiring lock"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
            let path = fixture.0.join("installer.lock");
            assert_eq!(
                acquire(&path).err().unwrap(),
                "An ODS installation is already in progress."
            );
            if mode == "death" {
                child.0.kill().unwrap();
            } else {
                std::fs::write(fixture.0.join("release"), b"release").unwrap();
            }
            let status = child.0.wait().unwrap();
            if mode != "death" {
                assert!(status.success(), "Helper failed ({mode})");
            }
            let file = acquire(&path).unwrap();
            drop(file);
            assert!(
                path.exists(),
                "The persistent lock file must not be unlinked"
            );
            assert!(acquire(&path).is_ok());
        }
    }

    #[test]
    fn reopening_preserves_lock_file_contents_and_io_errors_fail_closed() {
        let fixture = Fixture::new();
        let path = fixture.0.join("installer.lock");
        std::fs::write(&path, b"persistent").unwrap();
        let file = acquire(&path).unwrap();
        drop(file);
        assert_eq!(std::fs::read(&path).unwrap(), b"persistent");
        let invalid = fixture.0.join("directory");
        std::fs::create_dir(&invalid).unwrap();
        assert!(acquire(&invalid).err().unwrap().contains("Cannot open"));
        let invalid_parent = fixture.0.join("file-parent");
        std::fs::write(&invalid_parent, b"not a directory").unwrap();
        assert!(acquire(&invalid_parent.join("installer.lock"))
            .err()
            .unwrap()
            .contains("Cannot prepare"));
    }
}
