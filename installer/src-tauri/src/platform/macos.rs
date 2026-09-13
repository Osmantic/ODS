use super::SystemInfo;
use std::process::Command;

pub fn check_system() -> SystemInfo {
    let os_version = get_os_version();
    let ram_gb = get_ram_gb();
    let disk_free_gb = get_disk_free_gb();
    let hostname = Command::new("hostname")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown".into());

    SystemInfo {
        os: "macOS".into(),
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
    let out = Command::new("sw_vers").args(["-productVersion"]).output();
    match out {
        Ok(o) if o.status.success() => {
            format!("macOS {}", String::from_utf8_lossy(&o.stdout).trim())
        }
        _ => "macOS (unknown version)".into(),
    }
}

fn get_ram_gb() -> f64 {
    let out = Command::new("sysctl").args(["-n", "hw.memsize"]).output();
    match out {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout).trim().to_string();
            text.trim().parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0 * 1024.0)
        }
        _ => 0.0,
    }
}

fn get_disk_free_gb() -> f64 {
    let out = Command::new("df").args(["-g", "/"]).output();
    match out {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout);
            // df -g output: Filesystem 1G-blocks Used Available ...
            if let Some(line) = text.lines().nth(1) {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 4 {
                    return parts[3].parse().unwrap_or(0.0);
                }
            }
            0.0
        }
        _ => 0.0,
    }
}
