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

/// Ask PowerShell for the largest video controller and the 64-bit VRAM size
/// its driver publishes under the display adapter class key.
///
/// The registry row is matched to the chosen controller by DriverDesc so a
/// hybrid laptop cannot pair the iGPU's name with the discrete card's memory.
/// Both lookups are best-effort: a miss leaves QwMemorySize null, which is
/// what the AdapterRAM fallback in `windows_vram_mb` is for.
#[cfg(target_os = "windows")]
const WMI_GPU_QUERY: &str = concat!(
    "$c = Get-CimInstance Win32_VideoController | ",
    "Sort-Object AdapterRAM -Descending | Select-Object -First 1; ",
    "$qw = Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control",
    "\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\*' ",
    "-ErrorAction SilentlyContinue | ",
    "Where-Object { $_.DriverDesc -eq $c.Name } | Select-Object -First 1 ",
    "-ExpandProperty 'HardwareInformation.qwMemorySize' ",
    "-ErrorAction SilentlyContinue; ",
    "[pscustomobject]@{ Name = $c.Name; AdapterRAM = $c.AdapterRAM; ",
    "DriverVersion = $c.DriverVersion; QwMemorySize = $qw } | ConvertTo-Json",
);

#[cfg(target_os = "windows")]
fn detect_windows() -> GpuInfo {
    // Try NVIDIA first
    if let Some(gpu) = try_nvidia_smi() {
        return gpu;
    }

    // Fall back to a WMI query for any GPU.
    //
    // Win32_VideoController.AdapterRAM is a uint32 of *bytes*, so it saturates
    // at 4 GiB: Windows reports 4293918720 for every card at or above that, so
    // an 8 GB Arc A770 and a 16 GB RX 7800 both read as "4095 MB". That is
    // below recommend_tier's 8 GB step, so every non-NVIDIA card large enough
    // to matter was pinned to the smallest tier on Windows.
    //
    // The display class registry key carries a 64-bit qwMemorySize, which is
    // what Task Manager reads. Prefer it, and keep AdapterRAM as the fallback
    // for drivers that do not publish it.
    let output = Command::new("powershell")
        .args(["-NoProfile", "-Command", WMI_GPU_QUERY])
        .output();

    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
            let name = val["Name"].as_str().unwrap_or("Unknown GPU").to_string();
            let vram = windows_vram_mb(
                val["QwMemorySize"].as_u64(),
                val["AdapterRAM"].as_u64(),
            );
            let driver = val["DriverVersion"].as_str().map(String::from);
            let vendor = classify_vendor(&name);
            return GpuInfo { vendor, name, vram_mb: vram, driver_version: driver };
        }
    }

    GpuInfo { vendor: GpuVendor::None, name: "No GPU detected".into(), vram_mb: 0, driver_version: None }
}

/// Resolve Windows VRAM in MB from the two sources, preferring the 64-bit one.
///
/// Free of any Windows API so it can be tested on any host. `qw_bytes` is the
/// registry's qwMemorySize; `adapter_ram_bytes` is the uint32 AdapterRAM, used
/// only when the registry has nothing and reported as it stands — a saturated
/// value is still a better floor than zero.
#[cfg(any(target_os = "windows", test))]
fn windows_vram_mb(qw_bytes: Option<u64>, adapter_ram_bytes: Option<u64>) -> u64 {
    qw_bytes
        .filter(|bytes| *bytes > 0)
        .or(adapter_ram_bytes)
        .unwrap_or(0)
        / (1024 * 1024)
}

// ---------------------------------------------------------------------------
// macOS: system_profiler
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn detect_macos() -> GpuInfo {
    let output = Command::new("system_profiler")
        .args(["SPDisplaysDataType", "-json"])
        .output();

    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(displays) = val["SPDisplaysDataType"].as_array() {
                if let Some(gpu) = displays.first() {
                    let name = gpu["sppci_model"].as_str().unwrap_or("Apple GPU").to_string();
                    // Apple Silicon reports unified memory; estimate GPU-available portion
                    let vram_str = gpu["spdisplays_vram"].as_str().unwrap_or("0");
                    let vram = parse_vram_string(vram_str);
                    return GpuInfo {
                        vendor: GpuVendor::Apple,
                        name,
                        vram_mb: vram,
                        driver_version: None,
                    };
                }
            }
        }
    }

    // Fallback: assume Apple Silicon with unified memory via sysctl
    let mem_output = Command::new("sysctl").args(["-n", "hw.memsize"]).output();
    if let Ok(out) = mem_output {
        let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if let Ok(bytes) = text.parse::<u64>() {
            // Apple Silicon shares ~75% of unified memory with GPU
            let gpu_share_mb = (bytes / (1024 * 1024)) * 3 / 4;
            return GpuInfo {
                vendor: GpuVendor::Apple,
                name: "Apple Silicon".into(),
                vram_mb: gpu_share_mb,
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

#[cfg(target_os = "macos")]
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
mod windows_vram_tests {
    use super::windows_vram_mb;

    /// What AdapterRAM reports for any card at or above 4 GiB.
    const SATURATED_ADAPTER_RAM: u64 = 4_293_918_720;

    fn gb(n: u64) -> u64 {
        n * 1024 * 1024 * 1024
    }

    #[test]
    fn the_registry_value_wins_over_the_saturated_one() {
        assert_eq!(
            windows_vram_mb(Some(gb(16)), Some(SATURATED_ADAPTER_RAM)),
            16384
        );
    }

    #[test]
    fn a_large_card_is_no_longer_pinned_below_the_first_tier_step() {
        // The bug: whatever is fitted, AdapterRAM alone reads as a sub-8GB card.
        let old_path = windows_vram_mb(None, Some(SATURATED_ADAPTER_RAM));
        assert!(old_path < 8192, "the old path really did under-report");

        let fixed = windows_vram_mb(Some(gb(16)), Some(SATURATED_ADAPTER_RAM));
        assert!(fixed >= 8192, "a 16GB card must clear the 8GB step");
    }

    #[test]
    fn adapter_ram_is_the_fallback_when_the_registry_is_silent() {
        assert_eq!(windows_vram_mb(None, Some(gb(2))), 2048);
        assert_eq!(windows_vram_mb(Some(0), Some(gb(2))), 2048);
    }

    #[test]
    fn neither_source_means_no_gpu_memory() {
        assert_eq!(windows_vram_mb(None, None), 0);
    }
}
