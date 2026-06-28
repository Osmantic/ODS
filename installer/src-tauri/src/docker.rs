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
    let out = Command::new("docker").args(["--version"]).output().ok()?;
    if out.status.success() {
        Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        None
    }
}

fn is_docker_running() -> bool {
    Command::new("docker")
        .args(["info"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn get_compose_version() -> Option<String> {
    // Try "docker compose" (v2 plugin) first
    let out = Command::new("docker")
        .args(["compose", "version", "--short"])
        .output()
        .ok()?;

    if out.status.success() {
        return Some(String::from_utf8_lossy(&out.stdout).trim().to_string());
    }

    // Fallback: docker-compose (standalone v1)
    let out = Command::new("docker-compose")
        .args(["--version"])
        .output()
        .ok()?;

    if out.status.success() {
        Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        None
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
