use crate::state::{GpuInfo, GpuVendor};
use std::process::Command;

/// Detect the primary GPU on this system.
pub fn detect() -> GpuInfo {
    #[cfg(target_os = "windows")]
    {
        detect_windows()
    }
    #[cfg(target_os = "macos")]
    {
        detect_macos()
    }
    #[cfg(target_os = "linux")]
    {
        detect_linux()
    }
}

/// Recommend a ODS tier based on detected GPU VRAM.
pub fn recommend_tier(gpu: &GpuInfo) -> u8 {
    match gpu.vram_mb {
        0 => 0,                    // CPU-only / cloud
        v if v < 8192 => 1,       // < 8GB
        v if v < 12288 => 1,      // 8GB — Tier 1
        v if v < 24576 => 2,      // 12-24GB — Tier 2
        v if v < 49152 => 3,      // 24-48GB — Tier 3
        _ => 4,                    // 48GB+ — Tier 4
    }
}

// ---------------------------------------------------------------------------
// Windows: try nvidia-smi first, then fall back to WMIC/PowerShell
// ---------------------------------------------------------------------------

#[cfg(target_os = "windows")]
fn detect_windows() -> GpuInfo {
    // Try NVIDIA first
    if let Some(gpu) = try_nvidia_smi() {
        return gpu;
    }

    // Fall back to PowerShell WMI query for any GPU
    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -First 1 Name, AdapterRAM, DriverVersion | ConvertTo-Json",
        ])
        .output();

    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
            let name = val["Name"].as_str().unwrap_or("Unknown GPU").to_string();
            let vram = val["AdapterRAM"].as_u64().unwrap_or(0) / (1024 * 1024);
            let driver = val["DriverVersion"].as_str().map(String::from);
            let vendor = classify_vendor(&name);
            return GpuInfo { vendor, name, vram_mb: vram, driver_version: driver };
        }
    }

    GpuInfo { vendor: GpuVendor::None, name: "No GPU detected".into(), vram_mb: 0, driver_version: None }
}

// ---------------------------------------------------------------------------
// macOS: system_profiler
// ---------------------------------------------------------------------------

#[cfg(any(target_os = "macos", test))]
fn parse_macos_gpu(profiler_json: &str, unified_memory_mb: Option<u64>) -> Option<GpuInfo> {
    let value = serde_json::from_str::<serde_json::Value>(profiler_json).ok()?;
    let displays = value["SPDisplaysDataType"].as_array()?;
    let display = displays
        .iter()
        .find(|entry| {
            entry["sppci_model"]
                .as_str()
                .map(classify_vendor)
                .is_some_and(|vendor| vendor == GpuVendor::Apple)
        })
        .or_else(|| displays.first())?;

    let name = display["sppci_model"]
        .as_str()
        .unwrap_or("Apple GPU")
        .to_string();
    let vendor = classify_vendor(&name);
    let reported_vram = display["spdisplays_vram"]
        .as_str()
        .map(parse_vram_string)
        .unwrap_or(0);
    let vram_mb = if reported_vram == 0 && vendor == GpuVendor::Apple {
        unified_memory_mb.map(|memory| memory * 3 / 4).unwrap_or(0)
    } else {
        reported_vram
    };

    Some(GpuInfo {
        vendor,
        name,
        vram_mb,
        driver_version: None,
    })
}

#[cfg(target_os = "macos")]
fn apple_unified_memory_mb() -> Option<u64> {
    let output = Command::new("sysctl")
        .args(["-n", "hw.memsize"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }

    let bytes = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<u64>()
        .ok()?;
    Some(bytes / (1024 * 1024))
}

#[cfg(target_os = "macos")]
fn detect_macos() -> GpuInfo {
    let unified_memory_mb = apple_unified_memory_mb();
    let output = Command::new("system_profiler")
        .args(["SPDisplaysDataType", "-json"])
        .output();

    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        if let Some(gpu) = parse_macos_gpu(&text, unified_memory_mb) {
            return gpu;
        }
    }

    if cfg!(target_arch = "aarch64") {
        if let Some(memory_mb) = unified_memory_mb {
            return GpuInfo {
                vendor: GpuVendor::Apple,
                name: "Apple Silicon".into(),
                vram_mb: memory_mb * 3 / 4,
                driver_version: None,
            };
        }
    }

    GpuInfo { vendor: GpuVendor::None, name: "No GPU detected".into(), vram_mb: 0, driver_version: None }
}

// ---------------------------------------------------------------------------
// Linux: nvidia-smi, rocm-smi, or lspci fallback
// ---------------------------------------------------------------------------

#[cfg(target_os = "linux")]
fn detect_linux() -> GpuInfo {
    if let Some(gpu) = try_nvidia_smi() {
        return gpu;
    }

    // Try AMD ROCm
    let output = Command::new("rocm-smi")
        .args(["--showmeminfo", "vram", "--json"])
        .output();

    if let Ok(out) = output {
        if out.status.success() {
            let text = String::from_utf8_lossy(&out.stdout);
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                // Parse first card's VRAM
                if let Some(obj) = val.as_object() {
                    for (_key, card) in obj {
                        if let Some(total) = card["VRAM Total Memory (B)"].as_str() {
                            let bytes: u64 = total.parse().unwrap_or(0);
                            let vram_mb = bytes / (1024 * 1024);
                            // Get card name from rocm-smi --showproductname
                            let name = get_amd_name().unwrap_or_else(|| "AMD GPU".into());
                            return GpuInfo {
                                vendor: GpuVendor::Amd,
                                name,
                                vram_mb,
                                driver_version: None,
                            };
                        }
                    }
                }
            }
        }
    }

    // Fallback: lspci
    let output = Command::new("lspci").output();
    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        for line in text.lines() {
            let lower = line.to_lowercase();
            if lower.contains("vga") || lower.contains("3d") || lower.contains("display") {
                let vendor = classify_vendor(line);
                if vendor != GpuVendor::None {
                    return GpuInfo {
                        vendor,
                        name: line.to_string(),
                        vram_mb: 0, // Can't determine from lspci
                        driver_version: None,
                    };
                }
            }
        }
    }

    GpuInfo { vendor: GpuVendor::None, name: "No GPU detected".into(), vram_mb: 0, driver_version: None }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

fn try_nvidia_smi() -> Option<GpuInfo> {
    let output = Command::new("nvidia-smi")
        .args(["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let text = String::from_utf8_lossy(&output.stdout);
    let line = text.lines().next()?;
    let parts: Vec<&str> = line.split(", ").collect();

    if parts.len() >= 3 {
        let name = parts[0].trim().to_string();
        let vram_mb: u64 = parts[1].trim().parse().unwrap_or(0);
        let driver = parts[2].trim().to_string();
        Some(GpuInfo {
            vendor: GpuVendor::Nvidia,
            name,
            vram_mb,
            driver_version: Some(driver),
        })
    } else {
        None
    }
}

#[cfg(target_os = "linux")]
fn get_amd_name() -> Option<String> {
    let out = Command::new("rocm-smi")
        .args(["--showproductname"])
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    for line in text.lines() {
        if line.contains("Card series:") {
            return Some(line.split(':').nth(1)?.trim().to_string());
        }
    }
    None
}

fn classify_vendor(name: &str) -> GpuVendor {
    let lower = name.to_lowercase();
    if lower.contains("nvidia") || lower.contains("geforce") || lower.contains("rtx") || lower.contains("gtx") || lower.contains("quadro") || lower.contains("tesla") {
        GpuVendor::Nvidia
    } else if lower.contains("amd") || lower.contains("radeon") || lower.contains("rx ") {
        GpuVendor::Amd
    } else if lower.contains("intel") && (lower.contains("arc") || lower.contains("xe")) {
        GpuVendor::Intel
    } else if lower.contains("apple") || lower.contains("m1") || lower.contains("m2") || lower.contains("m3") || lower.contains("m4") {
        GpuVendor::Apple
    } else {
        GpuVendor::None
    }
}

#[cfg(any(target_os = "macos", test))]
fn parse_vram_string(s: &str) -> u64 {
    // Apple reports like "16 GB" or "8192 MB"
    let parts: Vec<&str> = s.split_whitespace().collect();
    if parts.len() >= 2 {
        let num: u64 = parts[0].parse().unwrap_or(0);
        match parts[1].to_uppercase().as_str() {
            "GB" => num * 1024,
            "MB" => num,
            _ => num,
        }
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apple_silicon_without_dedicated_vram_uses_unified_memory() {
        let profiler = r#"{
            "SPDisplaysDataType": [{
                "sppci_model": "Apple M3 Pro",
                "spdisplays_vendor": "Apple (0x106b)"
            }]
        }"#;

        let gpu = parse_macos_gpu(profiler, Some(16 * 1024)).unwrap();

        assert_eq!(gpu.vendor, GpuVendor::Apple);
        assert_eq!(gpu.name, "Apple M3 Pro");
        assert_eq!(gpu.vram_mb, 12 * 1024);
        assert_eq!(recommend_tier(&gpu), 2);
    }

    #[test]
    fn explicit_vram_wins_over_unified_memory_estimate() {
        let profiler = r#"{
            "SPDisplaysDataType": [{
                "sppci_model": "Apple M2 Ultra",
                "spdisplays_vram": "24 GB"
            }]
        }"#;

        let gpu = parse_macos_gpu(profiler, Some(96 * 1024)).unwrap();

        assert_eq!(gpu.vram_mb, 24 * 1024);
        assert_eq!(recommend_tier(&gpu), 3);
    }

    #[test]
    fn apple_gpu_is_preferred_when_profiler_lists_multiple_adapters() {
        let profiler = r#"{
            "SPDisplaysDataType": [
                {"sppci_model": "External Display Adapter", "spdisplays_vram": "1 GB"},
                {"sppci_model": "Apple M4 Max"}
            ]
        }"#;

        let gpu = parse_macos_gpu(profiler, Some(64 * 1024)).unwrap();

        assert_eq!(gpu.name, "Apple M4 Max");
        assert_eq!(gpu.vram_mb, 48 * 1024);
        assert_eq!(recommend_tier(&gpu), 4);
    }
}
