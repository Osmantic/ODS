#[cfg(target_os = "windows")]
pub mod windows;
#[cfg(target_os = "macos")]
pub mod macos;
#[cfg(target_os = "linux")]
pub mod linux;

use serde::Serialize;

const MINIMUM_RAM_GB: f64 = 8.0;
const MINIMUM_RAM_CHECK_GB: f64 = 7.5; // Allow 0.5 GB tolerance
const MINIMUM_DISK_GB: f64 = 20.0;
const SUPPORTED_ARCHITECTURES: &[&str] = &["x86_64", "aarch64", "arm64"];

#[derive(Debug, Serialize)]
pub struct SystemInfo {
    pub os: String,
    pub os_version: String,
    pub arch: String,
    pub ram_gb: f64,
    pub disk_free_gb: f64,
    pub hostname: String,
    pub wsl2_available: Option<bool>,  // Windows only
    pub wsl2_installed: Option<bool>,  // Windows only
}

/// Gather system information for the current platform.
pub fn check_system() -> SystemInfo {
    #[cfg(target_os = "windows")]
    { windows::check_system() }
    #[cfg(target_os = "macos")]
    { macos::check_system() }
    #[cfg(target_os = "linux")]
    { linux::check_system() }
}

/// Check if the system meets minimum requirements.
#[derive(Debug, Serialize)]
pub struct RequirementCheck {
    pub name: String,
    pub met: bool,
    pub found: String,
    pub required: String,
    pub help: Option<String>,
}

pub fn check_requirements(info: &SystemInfo) -> Vec<RequirementCheck> {
    let mut checks = vec![];

    // RAM: minimum 8GB
    checks.push(RequirementCheck {
        name: "RAM".into(),
        met: info.ram_gb >= MINIMUM_RAM_CHECK_GB,
        found: format!("{:.1} GB", info.ram_gb),
        required: format!("{} GB minimum", MINIMUM_RAM_GB as i32),
        help: if info.ram_gb < MINIMUM_RAM_CHECK_GB {
            Some("ODS needs at least 8GB RAM. Close memory-heavy apps or consider cloud mode.".into())
        } else {
            None
        },
    });

    // Disk: minimum 20GB free
    checks.push(RequirementCheck {
        name: "Disk Space".into(),
        met: info.disk_free_gb >= MINIMUM_DISK_GB,
        found: format!("{:.1} GB free", info.disk_free_gb),
        required: format!("{} GB minimum", MINIMUM_DISK_GB as i32),
        help: if info.disk_free_gb < MINIMUM_DISK_GB {
            Some("Free up disk space. Docker images and AI models require significant storage.".into())
        } else {
            None
        },
    });

    // Architecture
    let arch_ok = SUPPORTED_ARCHITECTURES.contains(&info.arch.as_str());
    checks.push(RequirementCheck {
        name: "Architecture".into(),
        met: arch_ok,
        found: info.arch.clone(),
        required: "x86_64 or ARM64".into(),
        help: if !arch_ok {
            Some("Your CPU architecture may not be supported.".into())
        } else {
            None
        },
    });

    // Windows-specific: WSL2
    if cfg!(target_os = "windows") {
        if let Some(installed) = info.wsl2_installed {
            checks.push(RequirementCheck {
                name: "WSL2".into(),
                met: installed,
                found: if installed { "Installed".into() } else { "Not installed".into() },
                required: "Required for Windows".into(),
                help: if !installed {
                    Some("We'll install WSL2 for you — this requires a one-time restart.".into())
                } else {
                    None
                },
            });
        }
    }

    checks
}
