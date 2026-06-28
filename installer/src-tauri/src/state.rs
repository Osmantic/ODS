use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

/// Persisted install state — survives reboots (e.g. after WSL2 install).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstallState {
    pub phase: InstallPhase,
    pub install_dir: Option<String>,
    pub detected_gpu: Option<GpuInfo>,
    pub selected_tier: Option<u8>,
    pub selected_features: Vec<String>,
    pub error: Option<String>,
    pub progress_pct: u8,
    pub progress_message: String,
    pub reboot_pending: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum InstallPhase {
    Welcome,
    SystemCheck,
    Prerequisites,
    GpuDetection,
    FeatureSelection,
    Installing,
    Complete,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GpuInfo {
    pub vendor: GpuVendor,
    pub name: String,
    pub vram_mb: u64,
    pub driver_version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum GpuVendor {
    Nvidia,
    Amd,
    Intel,
    Apple,
    None,
}

impl Default for InstallState {
    fn default() -> Self {
        Self {
            phase: InstallPhase::Welcome,
            install_dir: None,
            detected_gpu: None,
            selected_tier: None,
            selected_features: vec![],
            error: None,
            progress_pct: 0,
            progress_message: String::new(),
            reboot_pending: false,
        }
    }
}

impl InstallState {
    fn state_path() -> PathBuf {
        let dir = dirs_next().join("ods");
        let _ = fs::create_dir_all(&dir);
        dir.join("installer-state.json")
    }

    pub fn save(&self) -> Result<(), String> {
        let path = Self::state_path();
        let tmp = path.with_extension("json.tmp");
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(&tmp, json).map_err(|e| e.to_string())?;
        match fs::rename(&tmp, &path) {
            Ok(()) => Ok(()),
            Err(err) if path.exists() => {
                fs::remove_file(&path).map_err(|remove_err| remove_err.to_string())?;
                fs::rename(&tmp, &path).map_err(|rename_err| {
                    format!(
                        "Failed to replace installer state after rename error ({err}): {rename_err}"
                    )
                })
            }
            Err(err) => Err(err.to_string()),
        }
    }
}

impl InstallState {
    pub fn load() -> Option<InstallState> {
        let path = Self::state_path();
        let data = fs::read_to_string(path).ok()?;
        serde_json::from_str(&data).ok()
    }
}

fn dirs_next() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("C:\\ProgramData"))
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(home).join("Library/Application Support")
    }
    #[cfg(target_os = "linux")]
    {
        std::env::var("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
                PathBuf::from(home).join(".local/share")
            })
    }
}
