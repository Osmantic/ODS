use super::SystemInfo;
use std::process::Command;

pub fn check_system() -> SystemInfo {
    let os_version = get_os_version();
    let ram_gb = get_ram_gb();
    let disk_free_gb = get_disk_free_gb();
    let hostname = hostname::get()
        .map(|h| h.to_string_lossy().to_string())
        .unwrap_or_else(|_| "unknown".into());

    let wsl2_installed = check_wsl2_installed();

    SystemInfo {
        os: "Windows".into(),
        os_version,
        arch: std::env::consts::ARCH.into(),
        ram_gb,
        disk_free_gb,
        hostname,
        wsl2_available: Some(true), // All modern Windows 10/11 support WSL2
        wsl2_installed: Some(wsl2_installed),
    }
}

fn get_os_version() -> String {
    let out = Command::new("powershell")
        .args(["-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).Caption"])
        .output();
    match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => "Windows (unknown version)".into(),
    }
}

fn get_ram_gb() -> f64 {
    let out = Command::new("powershell")
        .args(["-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout).trim().to_string();
            parse_bytes_to_gb(&text, "TotalPhysicalMemory")
        }
        _ => 0.0,
    }
}

fn get_disk_free_gb() -> f64 {
    let out = Command::new("powershell")
        .args(["-NoProfile", "-Command", "(Get-PSDrive C).Free"])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let text = String::from_utf8_lossy(&o.stdout).trim().to_string();
            parse_bytes_to_gb(&text, "Get-PSDrive C free space")
        }
        _ => 0.0,
    }
}

fn parse_bytes_to_gb(text: &str, source: &str) -> f64 {
    match text.parse::<f64>() {
        Ok(bytes) => bytes / (1024.0 * 1024.0 * 1024.0),
        Err(_) => {
            eprintln!("Unrecognized {source} value from PowerShell: {text}");
            0.0
        }
    }
}

fn check_wsl2_installed() -> bool {
    let out = Command::new("wsl").args(["--status"]).output();
    match out {
        Ok(o) => o.status.success(),
        Err(_) => false,
    }
}

/// Install WSL2 on Windows. Returns true if a reboot is required.
pub fn install_wsl2() -> Result<bool, String> {
    let out = Command::new("powershell")
        .args(["-NoProfile", "-Command", "wsl --install --no-distribution"])
        .output()
        .map_err(|e| format!("Failed to run WSL install: {}", e))?;

    if out.status.success() {
        let text = String::from_utf8_lossy(&out.stdout).to_lowercase();
        // WSL install usually requires a reboot
        let needs_reboot = text.contains("restart") || text.contains("reboot");
        Ok(needs_reboot)
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        Err(format!("WSL2 installation failed: {}", stderr))
    }
}

mod hostname {
    use std::ffi::OsString;
    pub fn get() -> Result<OsString, ()> {
        std::env::var_os("COMPUTERNAME").ok_or(())
    }
}
