use std::process::Command;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct DockerStatus {
    pub installed: bool,
    pub running: bool,
    pub version: Option<String>,
    pub compose_installed: bool,
    pub compose_version: Option<String>,
}

/// Check if Docker is installed and running.
pub fn check() -> DockerStatus {
    let version = get_docker_version();
    let installed = version.is_some();
    let running = if installed { is_docker_running() } else { false };
    let compose_version = get_compose_version();
    let compose_installed = compose_version.is_some();

    DockerStatus { installed, running, version, compose_installed, compose_version }
}

fn get_docker_version() -> Option<String> {
    probe_version(Command::new("docker").args(["--version"]))
}

fn is_docker_running() -> bool {
    Command::new("docker")
        .args(["info"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn get_compose_version() -> Option<String> {
    let version = probe_version(Command::new("docker").args(["compose", "version", "--short"]));
    if version.is_some() {
        return version;
    }

    // The Linux dispatcher supports a standalone Compose command. Windows and
    // macOS installers invoke docker compose directly and require that command.
    #[cfg(target_os = "linux")]
    {
        probe_version(Command::new("docker-compose").args(["--version"]))
    }
    #[cfg(not(target_os = "linux"))]
    {
        None
    }
}

fn probe_version(command: &mut Command) -> Option<String> {
    let out = command.output().ok()?;
    if !out.status.success() {
        return None;
    }
    let version = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if version.is_empty() {
        None
    } else {
        Some(version)
    }
}

#[cfg(test)]
mod tests {
    use super::{check, probe_version};
    use std::path::PathBuf;
    use std::process::Command;

    struct CommandFixtures {
        directory: PathBuf,
    }

    impl CommandFixtures {
        fn new() -> Self {
            let directory = std::env::temp_dir().join(format!(
                "ods-compose-command-test-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            std::fs::create_dir_all(&directory).unwrap();
            let fixtures = Self {
                directory: directory.clone(),
            };
            let source = directory.join("fixture.rs");
            std::fs::write(
                &source,
                r#"
use std::{env, fs, process};
fn main() {
    let exe = env::current_exe().unwrap();
    let args: Vec<String> = env::args().skip(1).collect();
    if exe.file_stem().unwrap() == "docker-compose" {
        assert_eq!(args, ["--version"]);
        println!("docker-compose version 1.29.2");
        return;
    }
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["--version"] => println!("Docker version 27.5.1"),
        ["info"] => (),
        ["compose", "version", "--short"] => {
            match fs::read_to_string(exe.parent().unwrap().join("mode")).unwrap().as_str() {
                "supported" => println!("5.5.0"),
                "empty" => (),
                "failed-output" => { println!("5.5.0"); process::exit(7); },
                _ => process::exit(1),
            }
        },
        _ => process::exit(99),
    }
}
"#,
            )
            .unwrap();
            let docker = directory.join(format!("docker{}", std::env::consts::EXE_SUFFIX));
            let mut compiler = Command::new("rustc");
            compiler.arg(source).arg("-o").arg(&docker);
            if let Ok(flags) = std::env::var("CARGO_ENCODED_RUSTFLAGS") {
                compiler.args(flags.split('\u{1f}').filter(|flag| !flag.is_empty()));
            } else if let Ok(flags) = std::env::var("RUSTFLAGS") {
                compiler.args(flags.split_whitespace());
            }
            if let Ok(linker) = std::env::var("CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER") {
                compiler.arg("-C").arg(format!("linker={}", linker));
            }
            let output = compiler
                .output()
                .expect("compile harmless command fixtures with Rust toolchain");
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            std::fs::copy(
                &docker,
                directory.join(format!("docker-compose{}", std::env::consts::EXE_SUFFIX)),
            )
            .unwrap();
            fixtures
        }
    }

    impl Drop for CommandFixtures {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.directory);
        }
    }

    #[test]
    fn readiness_requires_the_command_used_by_installers() {
        let directory = match std::env::var_os("ODS_DOCKER_FIXTURE_CHILD") {
            Some(directory) => PathBuf::from(directory),
            None => {
                let fixtures = CommandFixtures::new();
                // Windows searches the executable directory before PATH. Run
                // only this test from beside the fixtures, with no host PATH.
                let driver = fixtures
                    .directory
                    .join(format!("probe-tests{}", std::env::consts::EXE_SUFFIX));
                std::fs::copy(std::env::current_exe().unwrap(), &driver).unwrap();
                #[cfg(target_os = "windows")]
                {
                    // Native Tauri test binaries also load WebView2Loader.dll.
                    // Cargo normally supplies its directory through PATH.
                    let current_exe = std::env::current_exe().unwrap();
                    let executable_directory = current_exe.parent().unwrap();
                    for directory in [executable_directory, executable_directory.parent().unwrap()]
                    {
                        let loader = directory.join("WebView2Loader.dll");
                        if loader.is_file() {
                            std::fs::copy(&loader, fixtures.directory.join("WebView2Loader.dll"))
                                .unwrap();
                        }
                    }
                }
                let output = Command::new(driver)
                    .args([
                        "readiness_requires_the_command_used_by_installers",
                        "--nocapture",
                    ])
                    .current_dir(&fixtures.directory)
                    .env("PATH", &fixtures.directory)
                    .env("ODS_DOCKER_FIXTURE_CHILD", &fixtures.directory)
                    .output()
                    .unwrap();
                assert!(
                    output.status.success(),
                    "Child status: {}\n{}\n{}",
                    output.status,
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
                return;
            }
        };
        assert_eq!(
            std::env::current_exe().unwrap().parent(),
            Some(directory.as_path())
        );
        for mode in ["missing", "empty", "failed-output", "supported"] {
            std::fs::write(directory.join("mode"), mode).unwrap();
            let status = check();
            assert_eq!(status.version.as_deref(), Some("Docker version 27.5.1"));
            assert!(status.installed && status.running);
            assert_eq!(
                status.compose_installed,
                mode == "supported" || cfg!(target_os = "linux"),
                "{}",
                mode
            );
            assert_eq!(
                status.compose_version,
                if mode == "supported" {
                    Some("5.5.0".into())
                } else if cfg!(target_os = "linux") {
                    Some("docker-compose version 1.29.2".into())
                } else {
                    None
                }
            );
        }
    }

    fn fixture(stdout: &str, exit_code: i32) -> Command {
        #[cfg(target_os = "windows")]
        {
            let mut command = Command::new("powershell.exe");
            command.args([
                "-NoProfile",
                "-Command",
                &format!("[Console]::Write('{}'); exit {}", stdout, exit_code),
            ]);
            command
        }
        #[cfg(not(target_os = "windows"))]
        {
            let mut command = Command::new("sh");
            command.args([
                "-c",
                &format!("printf '%s' '{}'; exit {}", stdout, exit_code),
            ]);
            command
        }
    }

    #[test]
    fn successful_command_requires_nonempty_version() {
        assert_eq!(
            probe_version(&mut fixture(" 5.5.0 \n", 0)),
            Some("5.5.0".into())
        );
        assert_eq!(probe_version(&mut fixture("", 0)), None);
        assert_eq!(probe_version(&mut fixture(" \n\t", 0)), None);
    }

    #[test]
    fn failed_command_cannot_report_a_version() {
        assert_eq!(probe_version(&mut fixture("5.5.0", 7)), None);
        assert_eq!(
            probe_version(&mut Command::new("ods-nonexistent-version-probe-fixture")),
            None
        );
    }
}

/// Get the Docker Desktop download URL for the current platform.
pub fn download_url() -> &'static str {
    #[cfg(target_os = "windows")]
    { "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" }
    #[cfg(target_os = "macos")]
    {
        if cfg!(target_arch = "aarch64") {
            "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
        } else {
            "https://desktop.docker.com/mac/main/amd64/Docker.dmg"
        }
    }
    #[cfg(target_os = "linux")]
    { "https://docs.docker.com/engine/install/" }
}

/// Return Docker installation guidance.
///
/// The desktop installer intentionally does not execute Docker's Linux
/// convenience script or downloaded Docker Desktop installers. Docker has
/// host-level privileges, so users should install it through a visible,
/// verifiable flow and then rerun prerequisite checks.
pub async fn install_docker() -> Result<String, String> {
    #[cfg(target_os = "linux")]
    {
        Err(format!(
            "For safety, the desktop installer does not run Docker's convenience script automatically.\n\nInstall Docker Engine using the official instructions, then rerun prerequisite checks:\n{}\n\nYou can also run ODS's shell installer from a terminal if you want the guided prerequisite flow.",
            download_url()
        ))
    }

    #[cfg(target_os = "windows")]
    {
        Err(format!(
            "For safety, the desktop installer does not download or run Docker Desktop automatically.\n\nInstall Docker Desktop manually, verify the installer publisher, then rerun prerequisite checks:\n{}",
            download_url()
        ))
    }

    #[cfg(target_os = "macos")]
    {
        Err(format!(
            "For safety, the desktop installer does not install Docker Desktop automatically.\n\nInstall Docker Desktop manually, then open it once from Applications before rerunning prerequisite checks:\n{}",
            download_url()
        ))
    }
}
