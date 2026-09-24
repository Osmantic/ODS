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
            text.parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0 * 1024.0)
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
            text.parse::<f64>().unwrap_or(0.0) / (1024.0 * 1024.0 * 1024.0)
        }
        _ => 0.0,
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
        // wsl.exe writes UTF-16LE; from_utf8_lossy would leave NUL bytes
        // between characters, so keyword checks could never match.
        let text = decode_wsl_output(&out.stdout).to_lowercase()
            + &decode_wsl_output(&out.stderr).to_lowercase();
        // WSL install usually requires a reboot
        let needs_reboot = text.contains("restart") || text.contains("reboot");
        Ok(needs_reboot)
    } else {
        let stderr = decode_wsl_output(&out.stderr);
        Err(format!("WSL2 installation failed: {}", stderr))
    }
}

/// Decode wsl.exe output, which is emitted as UTF-16LE (often with a BOM).
/// Anything without NUL bytes is treated as ordinary UTF-8.
fn decode_wsl_output(bytes: &[u8]) -> String {
    let wide: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
        .collect();
    if bytes.contains(&0)
        || wide.first() == Some(&0xFEFF)
    {
        let decoded = String::from_utf16_lossy(&wide);
        return decoded.trim_start_matches('\u{FEFF}').to_string();
    }
    String::from_utf8_lossy(bytes).into_owned()
}

mod hostname {
    use std::ffi::OsString;
    pub fn get() -> Result<OsString, ()> {
        std::env::var_os("COMPUTERNAME").ok_or(())
    }
}

#[cfg(test)]
mod tests {
    use super::decode_wsl_output;

    fn utf16le(text: &str) -> Vec<u8> {
        text.encode_utf16().flat_map(u16::to_le_bytes).collect()
    }

    #[test]
    fn wsl_utf16_reboot_message_is_decoded() {
        // The regression: from_utf8_lossy on this buffer can never contain
        // "reboot" because every ASCII char is followed by a NUL byte.
        let raw = utf16le("The requested operation is successful. \
            Changes will not be effective until the system is rebooted.");
        let decoded = decode_wsl_output(&raw);
        assert!(decoded.to_lowercase().contains("reboot"));
    }

    #[test]
    fn wsl_utf16_bom_is_stripped() {
        let mut raw = vec![0xFF, 0xFE];
        raw.extend(utf16le("please restart"));
        let decoded = decode_wsl_output(&raw);
        assert_eq!(decoded, "please restart");
    }

    #[test]
    fn plain_utf8_output_passes_through() {
        assert_eq!(decode_wsl_output(b"WSL is ready"), "WSL is ready");
    }

    #[test]
    fn empty_output_is_empty() {
        assert_eq!(decode_wsl_output(b""), "");
    }
}
