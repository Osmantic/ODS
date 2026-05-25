use crate::state::{InstallPhase, InstallState};
use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

const REPO_URL: &str = "https://github.com/Light-Heart-Labs/DreamServer.git";

#[derive(Debug, Clone, Serialize)]
pub struct ProgressEvent {
    pub phase: String,
    pub percent: u8,
    pub message: String,
}

/// Run the full DreamServer installation.
/// This clones the repo and delegates to the existing install-core.sh.
pub fn run_install(
    state: Arc<Mutex<InstallState>>,
    install_dir: PathBuf,
    tier: u8,
    features: Vec<String>,
) -> Result<(), String> {
    // Phase 1: Clone the repo
    update_progress(&state, "Downloading DreamServer", 5);

    if !install_dir.join("dream-server").exists() {
        let clone = Command::new("git")
            .args([
                "clone",
                "--depth",
                "1",
                REPO_URL,
                &install_dir.to_string_lossy(),
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|e| format!("Failed to clone repository: {}", e))?;

        if !clone.status.success() {
            let err = String::from_utf8_lossy(&clone.stderr);
            return Err(format!("Git clone failed: {}", err));
        }
    }

    update_progress(&state, "Configuring installation", 15);

    // Phase 2: Build installer arguments
    let dream_server_dir = install_dir.join("dream-server");
    let mut args = vec!["--tier".to_string(), tier.to_string()];

    if features.contains(&"voice".to_string()) {
        args.push("--voice".into());
    }
    if features.contains(&"workflows".to_string()) {
        args.push("--workflows".into());
    }
    if features.contains(&"rag".to_string()) {
        args.push("--rag".into());
    }
    if features.contains(&"image_gen".to_string()) {
        args.push("--image-gen".into());
    }
    if features.contains(&"all".to_string()) {
        args.push("--all".into());
    }

    // Phase 3: Run the installer with progress parsing
    update_progress(&state, "Running installer", 20);

    let install_script = dream_server_dir.join("install.sh");
    let install_ps1 = install_dir.join("install.ps1");

    // Make sure the script is executable
    #[cfg(not(target_os = "windows"))]
    {
        let _ = Command::new("chmod")
            .args(["+x", &install_script.to_string_lossy()])
            .output();
    }

    let mut child = if cfg!(target_os = "windows") {
        let mut ps_args = vec![
            "-NoProfile".to_string(),
            "-ExecutionPolicy".to_string(),
            "Bypass".to_string(),
            "-File".to_string(),
            install_ps1.to_string_lossy().to_string(),
            "-NonInteractive".to_string(),
            "-Tier".to_string(),
            tier.to_string(),
        ];

        for feature in &features {
            match feature.as_str() {
                "voice" => ps_args.push("-Voice".into()),
                "workflows" => ps_args.push("-Workflows".into()),
                "rag" => ps_args.push("-Rag".into()),
                "image_gen" => ps_args.push("-Comfyui".into()),
                "all" => ps_args.push("-All".into()),
                _ => {}
            }
        }

        Command::new("powershell.exe")
            .args(&ps_args)
            .current_dir(&install_dir)
            .env("DREAM_INSTALLER_GUI", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to start Windows installer: {}", e))?
    } else {
        Command::new(&install_script)
            .args(&args)
            .current_dir(&dream_server_dir)
            .env("DREAM_INSTALLER_GUI", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to start installer: {}", e))?
    };

    let stderr_handle = child.stderr.take().map(|stderr| {
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            reader
                .lines()
                .map_while(Result::ok)
                .collect::<Vec<String>>()
        })
    });

    // Parse stdout for progress updates
    if let Some(stdout) = child.stdout.take() {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                if let Some(progress) = parse_progress_line(&line) {
                    update_progress(&state, &progress.message, progress.percent);
                }
            }
        }
    }

    let output = child
        .wait()
        .map_err(|e| format!("Installer process error: {}", e))?;
    let stderr_lines = stderr_handle
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default();

    if output.success() {
        update_progress(&state, "Installation complete!", 100);
        let mut s = state.lock().unwrap();
        s.phase = InstallPhase::Complete;
        let _ = s.save();
        Ok(())
    } else {
        let detail = stderr_lines
            .iter()
            .rev()
            .take(10)
            .cloned()
            .collect::<Vec<String>>()
            .into_iter()
            .rev()
            .collect::<Vec<String>>()
            .join("\n");
        if detail.is_empty() {
            Err("Installation failed. Check logs for details.".into())
        } else {
            Err(format!("Installation failed:\n{}", detail))
        }
    }
}

/// Parse a progress line from the installer.
/// Expected format: DREAM_PROGRESS:<percent>:<message>
fn parse_progress_line(line: &str) -> Option<ProgressEvent> {
    if let Some(rest) = line.strip_prefix("DREAM_PROGRESS:") {
        let parts: Vec<&str> = rest.splitn(3, ':').collect();
        if parts.len() >= 2 {
            let percent = parts[0].parse().unwrap_or(0);
            let phase = if parts.len() >= 3 { parts[1] } else { "" };
            let message = if parts.len() >= 3 { parts[2] } else { parts[1] };
            return Some(ProgressEvent {
                phase: phase.to_string(),
                percent,
                message: message.to_string(),
            });
        }
    }

    // Also parse phase markers from the existing installer output
    let line_lower = line.to_lowercase();
    let progress = if line_lower.contains("preflight") {
        Some(("preflight", 20, "Running preflight checks"))
    } else if line_lower.contains("detecting") && line_lower.contains("gpu") {
        Some(("detection", 25, "Detecting GPU hardware"))
    } else if line_lower.contains("installing") && line_lower.contains("docker") {
        Some(("docker", 35, "Setting up Docker"))
    } else if line_lower.contains("pulling") || line_lower.contains("download") {
        Some(("images", 50, "Downloading container images"))
    } else if line_lower.contains("starting") && line_lower.contains("services") {
        Some(("services", 75, "Starting services"))
    } else if line_lower.contains("health") && line_lower.contains("check") {
        Some(("health", 85, "Checking service health"))
    } else if line_lower.contains("ready") || line_lower.contains("complete") {
        Some(("complete", 95, "Almost done"))
    } else {
        None
    };

    progress.map(|(phase, percent, message)| ProgressEvent {
        phase: phase.to_string(),
        percent,
        message: message.to_string(),
    })
}

fn update_progress(state: &Arc<Mutex<InstallState>>, message: &str, percent: u8) {
    if let Ok(mut s) = state.lock() {
        s.progress_pct = percent;
        s.progress_message = message.to_string();
        s.phase = InstallPhase::Installing;
        let _ = s.save();
    }
}

/// Default install directory per platform.
pub fn default_install_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        let home = std::env::var("USERPROFILE").unwrap_or_else(|_| "C:\\Users\\Public".into());
        PathBuf::from(home).join("DreamServer")
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(home).join("DreamServer")
    }
    #[cfg(target_os = "linux")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(home).join("DreamServer")
    }
}
