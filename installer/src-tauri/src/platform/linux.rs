use super::SystemInfo;
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
    let out = Command::new("df")
        .args(["--output=avail", "-BG", "/"])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout);
            if let Some(line) = text.lines().nth(1) {
                let value = line.trim().trim_end_matches('G');
                return match value.parse() {
                    Ok(avail) => avail,
                    Err(_) => {
                        eprintln!("Unrecognized df avail value: {value}");
                        0.0
                    }
                };
            }
            0.0
        }
        _ => 0.0,
    }
}
