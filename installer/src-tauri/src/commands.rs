use crate::state::{GpuInfo, InstallPhase, InstallState};
use crate::{docker, gpu, installer, platform};
use serde::Serialize;
use std::sync::Mutex;

const ALLOWED_FEATURES: &[&str] = &["voice", "workflows", "rag", "image_gen", "all"];

// ---- System Check ----

#[derive(Serialize)]
pub struct SystemCheckResult {
    pub system: platform::SystemInfo,
    pub requirements: Vec<platform::RequirementCheck>,
    pub docker: docker::DockerStatus,
}

#[tauri::command]
pub fn check_system() -> SystemCheckResult {
    let system = platform::check_system();
    let requirements = platform::check_requirements(&system);
    let docker = docker::check();

    SystemCheckResult {
        system,
        requirements,
        docker,
    }
}

// ---- Prerequisites ----

#[derive(Serialize)]
pub struct PrerequisiteStatus {
    pub git_installed: bool,
    pub docker_installed: bool,
    pub docker_running: bool,
    pub wsl2_needed: bool,
    pub wsl2_installed: bool,
    pub all_met: bool,
}

#[tauri::command]
pub fn check_prerequisites() -> PrerequisiteStatus {
    let git = which::which("git").is_ok();
    let docker_status = docker::check();
    let wsl2_needed = cfg!(target_os = "windows");
    let wsl2_installed = if wsl2_needed {
        std::process::Command::new("wsl")
            .args(["--status"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    } else {
        true
    };

    let all_met = git
        && docker_status.installed
        && docker_status.running
        && docker_status.compose_installed
        && (!wsl2_needed || wsl2_installed);

    PrerequisiteStatus {
        git_installed: git,
        docker_installed: docker_status.installed,
        docker_running: docker_status.running,
        wsl2_needed,
        wsl2_installed,
        all_met,
    }
}

// ---- Install Prerequisites ----

#[derive(Serialize)]
pub struct InstallPrereqResult {
    pub success: bool,
    pub message: String,
    pub reboot_required: bool,
}

#[tauri::command]
pub async fn install_prerequisites(component: String) -> InstallPrereqResult {
    match component.as_str() {
        "docker" => match docker::install_docker().await {
            Ok(msg) => InstallPrereqResult {
                success: true,
                message: msg,
                reboot_required: false,
            },
            Err(msg) => InstallPrereqResult {
                success: false,
                message: msg,
                reboot_required: false,
            },
        },
        #[cfg(target_os = "windows")]
        "wsl2" => match crate::platform::windows::install_wsl2() {
            Ok(needs_reboot) => InstallPrereqResult {
                success: true,
                message: if needs_reboot {
                    "WSL2 installed. A restart is required to complete setup.".into()
                } else {
                    "WSL2 is ready.".into()
                },
                reboot_required: needs_reboot,
            },
            Err(msg) => InstallPrereqResult {
                success: false,
                message: msg,
                reboot_required: false,
            },
        },
        _ => InstallPrereqResult {
            success: false,
            message: format!("Unknown component: {}", component),
            reboot_required: false,
        },
    }
}

// ---- GPU Detection ----

#[derive(Serialize)]
pub struct GpuResult {
    pub gpu: GpuInfo,
    pub recommended_tier: u8,
    pub tier_description: String,
}

#[tauri::command]
pub fn detect_gpu() -> GpuResult {
    let gpu = gpu::detect();
    let tier = gpu::recommend_tier(&gpu);
    let desc = tier_description(tier);

    GpuResult {
        gpu,
        recommended_tier: tier,
        tier_description: desc,
    }
}

fn tier_description(tier: u8) -> String {
    match tier {
        0 => "Cloud Mode — No local GPU detected. Uses cloud AI providers.".into(),
        1 => "Tier 1 — Qwen3-8B (8GB VRAM). Great for chat, code help, and general tasks.".into(),
        2 => "Tier 2 — Qwen3-14B (12GB+ VRAM). Stronger reasoning and longer context.".into(),
        3 => "Tier 3 — Qwen3-32B (24GB+ VRAM). Professional-grade for complex tasks.".into(),
        4 => "Tier 4 — Qwen3-72B (48GB+ VRAM). Enterprise-level, best quality.".into(),
        _ => "Unknown tier".into(),
    }
}

// ---- Installation ----

#[tauri::command]
pub async fn start_install(
    tier: u8,
    features: Vec<String>,
    install_dir: Option<String>,
) -> Result<String, String> {
    validate_install_request(tier, &features)?;

    let dir = install_dir
        .map(std::path::PathBuf::from)
        .unwrap_or_else(installer::default_install_dir);

    let state = std::sync::Arc::new(Mutex::new(InstallState {
        phase: InstallPhase::Installing,
        install_dir: Some(dir.to_string_lossy().to_string()),
        selected_tier: Some(tier),
        selected_features: features.clone(),
        ..Default::default()
    }));

    let state_clone = state.clone();

    // Run installation in a blocking thread
    tokio::task::spawn_blocking(move || installer::run_install(state_clone, dir, tier, features))
        .await
        .map_err(|e| format!("Install task failed: {}", e))?
        .map(|_| "Installation complete!".to_string())
}

fn validate_install_request(tier: u8, features: &[String]) -> Result<(), String> {
    if tier > 4 {
        return Err(format!("Unsupported install tier: {}", tier));
    }

    for feature in features {
        if !ALLOWED_FEATURES.contains(&feature.as_str()) {
            return Err(format!("Unsupported feature: {}", feature));
        }
    }

    Ok(())
}

// ---- Progress ----

#[tauri::command]
pub fn get_install_progress() -> ProgressInfo {
    // Read from persisted state
    let state_path = state_file_path();
    if let Ok(data) = std::fs::read_to_string(&state_path) {
        if let Ok(state) = serde_json::from_str::<InstallState>(&data) {
            return ProgressInfo {
                phase: format!("{:?}", state.phase),
                percent: state.progress_pct,
                message: state.progress_message,
                error: state.error,
            };
        }
    }

    ProgressInfo {
        phase: "unknown".into(),
        percent: 0,
        message: "Waiting for installer...".into(),
        error: None,
    }
}

#[derive(Serialize)]
pub struct ProgressInfo {
    pub phase: String,
    pub percent: u8,
    pub message: String,
    pub error: Option<String>,
}

// ---- State ----

#[tauri::command]
pub fn get_install_state() -> InstallState {
    let state_path = state_file_path();
    if let Ok(data) = std::fs::read_to_string(&state_path) {
        if let Ok(state) = serde_json::from_str::<InstallState>(&data) {
            return state;
        }
    }
    InstallState::default()
}

// ---- Open DreamServer ----

#[tauri::command]
pub fn open_dreamserver() -> Result<(), String> {
    let url = "http://localhost:3000";
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/C", "start", url])
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(url)
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(url)
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
    }
    Ok(())
}

// ---- Helpers ----

fn state_file_path() -> std::path::PathBuf {
    #[cfg(target_os = "windows")]
    {
        let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| "C:\\ProgramData".into());
        std::path::PathBuf::from(base)
            .join("dreamserver")
            .join("installer-state.json")
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        std::path::PathBuf::from(home)
            .join("Library/Application Support/dreamserver/installer-state.json")
    }
    #[cfg(target_os = "linux")]
    {
        let base = std::env::var("XDG_DATA_HOME").unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{}/.local/share", home)
        });
        std::path::PathBuf::from(base)
            .join("dreamserver")
            .join("installer-state.json")
    }
}
