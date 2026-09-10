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
    probe_version("docker", &["--version"])
}

/// Run a version command, returning its trimmed stdout only if it succeeded.
///
/// Returns None for every kind of failure — the binary is missing, it exited
/// non-zero, or it printed nothing — so callers can chain probes with or_else.
fn probe_version(program: &str, args: &[&str]) -> Option<String> {
    let out = Command::new(program).args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
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
    // The v2 plugin first, then the standalone v1 binary. The two probes have to
    // be independent: `?` on the v2 output() returned from the whole function
    // when spawning failed, and spawning fails outright when there is no docker
    // binary — so a host running Podman or carrying only docker-compose reported
    // no Compose at all, and the Prerequisites page offered no way forward.
    probe_version("docker", &["compose", "version", "--short"])
        .or_else(|| probe_version("docker-compose", &["--version"]))
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

#[cfg(test)]
mod tests {
    use super::*;

    /// A name no host has on PATH, standing in for an uninstalled docker.
    const MISSING: &str = "ods-installer-no-such-binary";

    #[test]
    fn probe_version_reports_nothing_when_the_binary_is_missing() {
        assert_eq!(probe_version(MISSING, &["--version"]), None);
    }

    #[cfg(unix)]
    #[test]
    fn probe_version_trims_the_output() {
        assert_eq!(probe_version("echo", &["1.29.2"]).as_deref(), Some("1.29.2"));
    }

    #[cfg(unix)]
    #[test]
    fn probe_version_rejects_a_failed_command() {
        assert_eq!(probe_version("false", &[]), None);
    }

    #[cfg(unix)]
    #[test]
    fn probe_version_rejects_empty_output() {
        assert_eq!(probe_version("true", &[]), None);
    }

    #[cfg(unix)]
    #[test]
    fn a_missing_first_probe_does_not_abort_the_chain() {
        // The regression: an unspawnable first probe used to end the lookup, so
        // the standalone docker-compose fallback was never reached.
        let version = probe_version(MISSING, &["compose", "version", "--short"])
            .or_else(|| probe_version("echo", &["docker-compose version 1.29.2"]));
        assert_eq!(version.as_deref(), Some("docker-compose version 1.29.2"));
    }
}
