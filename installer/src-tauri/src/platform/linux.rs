use super::SystemInfo;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn check_system() -> SystemInfo {
    let os_version = get_os_version();
    let ram_gb = get_ram_gb();
    let disk_free_gb = get_disk_free_gb();
    let hostname = std::fs::read_to_string("/etc/hostname")
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "unknown".into());

    SystemInfo {
        os: "Linux".into(),
        os_version,
        arch: std::env::consts::ARCH.into(),
        ram_gb,
        disk_free_gb,
        hostname,
        wsl2_available: None,
        wsl2_installed: None,
    }
}

fn get_os_version() -> String {
    // Try /etc/os-release first
    if let Ok(content) = std::fs::read_to_string("/etc/os-release") {
        for line in content.lines() {
            if line.starts_with("PRETTY_NAME=") {
                return line
                    .trim_start_matches("PRETTY_NAME=")
                    .trim_matches('"')
                    .to_string();
            }
        }
    }

    let out = Command::new("uname").args(["-sr"]).output();
    match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => "Linux (unknown version)".into(),
    }
}

fn get_ram_gb() -> f64 {
    if let Ok(content) = std::fs::read_to_string("/proc/meminfo") {
        for line in content.lines() {
            if line.starts_with("MemTotal:") {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    let kb: f64 = parts[1].parse().unwrap_or(0.0);
                    return kb / (1024.0 * 1024.0);
                }
            }
        }
    }
    0.0
}

fn get_disk_free_gb() -> f64 {
    let probe_path =
        install_filesystem_probe_path(std::env::var_os("HOME").map(PathBuf::from));
    get_disk_free_gb_at(&probe_path)
}

fn install_filesystem_probe_path(home: Option<PathBuf>) -> PathBuf {
    home.filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| PathBuf::from("/"))
}

fn get_disk_free_gb_at(path: &Path) -> f64 {
    let out = Command::new("df")
        .args(["--output=avail", "-BG", "--"])
        .arg(path)
        .output();
    match out {
        Ok(o) if o.status.success() => parse_available_gb(&String::from_utf8_lossy(&o.stdout)),
        _ => 0.0,
    }
}

fn parse_available_gb(output: &str) -> f64 {
    output
        .lines()
        .nth(1)
        .and_then(|line| line.trim().trim_end_matches('G').parse().ok())
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disk_check_targets_the_default_install_filesystem() {
        assert_eq!(
            install_filesystem_probe_path(Some(PathBuf::from("/mnt/home/alice"))),
            PathBuf::from("/mnt/home/alice")
        );
        assert_eq!(install_filesystem_probe_path(None), PathBuf::from("/"));
    }

    #[test]
    fn parses_gnu_df_available_gigabytes() {
        assert_eq!(parse_available_gb("Avail\n42G\n"), 42.0);
        assert_eq!(parse_available_gb("unexpected"), 0.0);
    }
}
