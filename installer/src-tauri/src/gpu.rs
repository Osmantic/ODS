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

#[cfg(target_os = "macos")]
fn detect_macos() -> GpuInfo {
    let profiled = profile_macos_gpu();

    // A discrete or external GPU reports its own VRAM. Apple Silicon does not:
    // the GPU draws on unified memory, so SPDisplaysDataType carries no vram key
    // at all. Reading that missing key as "0" and returning right here left every
    // Apple Silicon Mac on vram_mb 0, which recommend_tier answers with tier 0 —
    // Cloud Mode — and made the hw.memsize fallback below unreachable.
    let vram_mb = match profiled.as_ref().and_then(|(_, vram)| *vram) {
        Some(vram) => vram,
        None => unified_memory_share_mb(),
    };

    match profiled {
        Some((name, _)) => GpuInfo {
            vendor: GpuVendor::Apple,
            name,
            vram_mb,
            driver_version: None,
        },
        // system_profiler is missing or unparseable, but sysctl still answered.
        None if vram_mb > 0 => GpuInfo {
            vendor: GpuVendor::Apple,
            name: "Apple Silicon".into(),
            vram_mb,
            driver_version: None,
        },
        None => GpuInfo {
            vendor: GpuVendor::None,
            name: "No GPU detected".into(),
            vram_mb: 0,
            driver_version: None,
        },
    }
}

/// The primary GPU's name and, when it has dedicated video memory, its size in MB.
#[cfg(target_os = "macos")]
fn profile_macos_gpu() -> Option<(String, Option<u64>)> {
    let out = Command::new("system_profiler")
        .args(["SPDisplaysDataType", "-json"])
        .output()
        .ok()?;
    let val: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    let gpu = val["SPDisplaysDataType"].as_array()?.first()?;
    Some((macos_gpu_name(gpu), macos_gpu_vram_mb(gpu)))
}

#[cfg(target_os = "macos")]
fn macos_gpu_name(gpu: &serde_json::Value) -> String {
    // sppci_model is the documented key; _name is what the JSON output actually
    // carries on some releases, and the two agree when both are present.
    for key in ["sppci_model", "_name"] {
        match gpu[key].as_str() {
            Some(name) if !name.trim().is_empty() => return name.trim().to_string(),
            _ => {}
        }
    }
    "Apple GPU".into()
}

/// Dedicated VRAM in MB, or None when the GPU shares system memory.
#[cfg(target_os = "macos")]
fn macos_gpu_vram_mb(gpu: &serde_json::Value) -> Option<u64> {
    // Intel-era Macs use spdisplays_vram, newer profiles prefix it, and an
    // integrated Intel GPU only reports the shared figure.
    for key in ["spdisplays_vram", "_spdisplays_vram", "spdisplays_vram_shared"] {
        if let Some(text) = gpu[key].as_str() {
            let mb = parse_vram_string(text);
            if mb > 0 {
                return Some(mb);
            }
        }
    }
    None
}

/// The share of unified memory Apple Silicon will let the GPU use.
#[cfg(target_os = "macos")]
fn unified_memory_share_mb() -> u64 {
    let Ok(out) = Command::new("sysctl").args(["-n", "hw.memsize"]).output() else {
        return 0;
    };
    String::from_utf8_lossy(&out.stdout)
        .trim()
        .parse::<u64>()
        // Metal will not hand out the whole machine; ~75% is the usual ceiling.
        .map(|bytes| (bytes / (1024 * 1024)) * 3 / 4)
        .unwrap_or(0)
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

/// Parse the VRAM figures system_profiler prints: "16 GB", "8192 MB", "1536MB".
#[cfg(target_os = "macos")]
fn parse_vram_string(s: &str) -> u64 {
    let s = s.trim();
    let digits: String = s.chars().take_while(|c| c.is_ascii_digit()).collect();
    let num: u64 = digits.parse().unwrap_or(0);
    // The unit is optional and may or may not be separated by a space; anything
    // that is not gigabytes is read as megabytes, which is what Apple emits.
    if s[digits.len()..].trim_start().to_ascii_uppercase().starts_with("GB") {
        num * 1024
    } else {
        num
    }
}

#[cfg(all(test, target_os = "macos"))]
mod macos_tests {
    use super::*;

    #[test]
    fn parse_vram_string_handles_apples_formats() {
        assert_eq!(parse_vram_string("16 GB"), 16 * 1024);
        assert_eq!(parse_vram_string("8192 MB"), 8192);
        assert_eq!(parse_vram_string("1536MB"), 1536);
        assert_eq!(parse_vram_string("0"), 0);
        assert_eq!(parse_vram_string(""), 0);
    }

    #[test]
    fn apple_silicon_reports_no_dedicated_vram() {
        // Verbatim shape of an Apple Silicon entry: there is no vram key.
        let gpu = serde_json::json!({
            "_name": "Apple M5 Pro",
            "sppci_model": "Apple M5 Pro",
            "sppci_cores": "20",
            "spdisplays_vendor": "sppci_vendor_Apple",
        });
        assert_eq!(macos_gpu_name(&gpu), "Apple M5 Pro");
        assert_eq!(macos_gpu_vram_mb(&gpu), None);
    }

    #[test]
    fn discrete_gpu_vram_is_read_from_the_profile() {
        let gpu = serde_json::json!({
            "sppci_model": "AMD Radeon Pro 5500M",
            "spdisplays_vram": "8 GB",
        });
        assert_eq!(macos_gpu_vram_mb(&gpu), Some(8 * 1024));
    }

    #[test]
    fn gpu_name_falls_back_to_the_underscore_key() {
        let gpu = serde_json::json!({ "_name": "Apple M1" });
        assert_eq!(macos_gpu_name(&gpu), "Apple M1");
        assert_eq!(macos_gpu_name(&serde_json::json!({})), "Apple GPU");
    }

    #[test]
    fn apple_silicon_does_not_land_in_cloud_mode() {
        // The regression this guards: unified memory has to reach recommend_tier
        // as something other than 0, or every Apple Silicon Mac gets Cloud Mode.
        let gpu = detect_macos();
        assert!(gpu.vram_mb > 0, "detected no usable VRAM: {:?}", gpu.vram_mb);
        assert!(recommend_tier(&gpu) > 0);
    }
}
