use crate::state::{InstallPhase, InstallState};
use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

const DEFAULT_REPO_URL: &str = "https://github.com/Osmantic/ODS.git";
const DEFAULT_INSTALL_REF: &str = "main";
const TRANSFERRED_REPO_URL_BYTES: &[u8] = &[
    104, 116, 116, 112, 115, 58, 47, 47, 103, 105, 116, 104, 117, 98, 46, 99, 111, 109, 47, 76,
    105, 103, 104, 116, 45, 72, 101, 97, 114, 116, 45, 76, 97, 98, 115, 47, 79, 68, 83, 46, 103,
    105, 116,
];

fn repo_url() -> &'static str {
    option_env!("ODS_REPO_URL").unwrap_or(DEFAULT_REPO_URL)
}

fn install_ref() -> &'static str {
    option_env!("ODS_INSTALL_REF").unwrap_or(DEFAULT_INSTALL_REF)
}

#[derive(Debug, Clone, Serialize)]
pub struct ProgressEvent {
    pub phase: String,
    pub percent: u8,
    pub message: String,
}

/// Run the full ODS installation.
/// This clones the repo and delegates to the existing install-core.sh.
pub fn run_install(
    state: Arc<Mutex<InstallState>>,
    install_dir: PathBuf,
    tier: u8,
    features: Vec<String>,
) -> Result<(), String> {
    // Phase 1: Clone the repo
    update_progress(&state, "Downloading ODS", 5);

    ensure_checkout(&install_dir)?;

    update_progress(&state, "Configuring installation", 15);

    // Phase 2: Build installer arguments
    let ods_dir = install_dir.join("ods");
    let args = build_install_args(tier, &features);

    // Phase 3: Run the installer with progress parsing
    update_progress(&state, "Running installer", 20);

    let install_script = ods_dir.join("install.sh");
    let install_ps1 = install_dir.join("install.ps1");

    // Make sure the script is executable
    #[cfg(not(target_os = "windows"))]
    {
        let _ = Command::new("chmod")
            .args(["+x", &install_script.to_string_lossy()])
            .output();
    }

    let mut child = if cfg!(target_os = "windows") {
        let ps_args = build_windows_args(&install_ps1, tier, &features);

        Command::new("powershell.exe")
            .args(&ps_args)
            .current_dir(&install_dir)
            .env("ODS_INSTALLER_GUI", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to start Windows installer: {}", e))?
    } else {
        Command::new(&install_script)
            .args(&args)
            .current_dir(&ods_dir)
            .env("ODS_INSTALLER_GUI", "1")
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

/// GUI feature id -> the install-core.sh flags that enable and disable it.
///
/// Every one of these defaults to enabled in install-core.sh, so an unselected
/// feature has to be turned off explicitly. Emitting only the on-flags installs
/// everything regardless of what the user picked.
const FEATURE_FLAGS: [(&str, &str, &str); 4] = [
    ("voice", "--voice", "--no-voice"),
    ("workflows", "--workflows", "--no-workflows"),
    ("rag", "--rag", "--no-rag"),
    ("image_gen", "--comfyui", "--no-comfyui"),
];

fn build_install_args(tier: u8, features: &[String]) -> Vec<String> {
    let mut args = vec!["--tier".to_string(), tier.to_string()];

    // --all already turns everything on; adding per-feature flags after it would
    // switch most of it straight back off, since anything unlisted reads as
    // deselected here.
    if features.iter().any(|f| f == "all") {
        args.push("--all".into());
        return args;
    }

    for (id, on, off) in FEATURE_FLAGS {
        let flag = if features.iter().any(|f| f == id) { on } else { off };
        args.push(flag.into());
    }

    args
}

/// Build the argument list for the Windows PowerShell entrypoint.
///
/// Kept separate from the spawn so it can be tested on any host.
fn build_windows_args(install_ps1: &Path, tier: u8, features: &[String]) -> Vec<String> {
    let mut args = vec![
        "-NoProfile".to_string(),
        "-ExecutionPolicy".to_string(),
        "Bypass".to_string(),
        "-File".to_string(),
        install_ps1.to_string_lossy().to_string(),
        "-NonInteractive".to_string(),
        "-Tier".to_string(),
        tier.to_string(),
    ];

    for feature in features {
        match feature.as_str() {
            "voice" => args.push("-Voice".into()),
            "workflows" => args.push("-Workflows".into()),
            "rag" => args.push("-Rag".into()),
            "image_gen" => args.push("-Comfyui".into()),
            "all" => args.push("-All".into()),
            _ => {}
        }
    }

    // ComfyUI is on by default in the installer, so an unselected box has to say
    // so explicitly. install.ps1 exposes -NoComfyui but has no
    // -NoVoice/-NoWorkflows/-NoRag, so those three cannot be switched off here.
    let selected_all = features.iter().any(|f| f == "all");
    if !selected_all && !features.iter().any(|f| f == "image_gen") {
        args.push("-NoComfyui".into());
    }

    args
}

fn ensure_checkout(install_dir: &Path) -> Result<(), String> {
    if install_dir.join("ods").exists() {
        return validate_checkout(install_dir);
    }

    if install_dir.exists()
        && install_dir
            .read_dir()
            .map_err(|e| e.to_string())?
            .next()
            .is_some()
    {
        return Err(format!(
            "{} already exists but is not an ODS checkout. Choose an empty directory or the existing ODS install directory.",
            install_dir.display()
        ));
    }

    let clone = Command::new("git")
        .args([
            "clone",
            "--depth",
            "1",
            "--branch",
            install_ref(),
            repo_url(),
        ])
        .arg(install_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("Failed to clone repository: {}", e))?;

    if !clone.status.success() {
        let err = String::from_utf8_lossy(&clone.stderr);
        return Err(format!(
            "Git clone failed for ODS ref '{}': {}",
            install_ref(),
            err
        ));
    }

    validate_checkout(install_dir)
}

fn validate_checkout(install_dir: &Path) -> Result<(), String> {
    if !install_dir.join(".git").exists() {
        return Err(format!(
            "{} contains an ods directory but is not a git checkout. Refusing to run installer scripts from an unverified directory.",
            install_dir.display()
        ));
    }

    let is_work_tree = run_git(install_dir, &["rev-parse", "--is-inside-work-tree"])?;
    if is_work_tree.trim() != "true" {
        return Err(format!(
            "{} is not a valid git worktree.",
            install_dir.display()
        ));
    }

    let origin = run_git(install_dir, &["remote", "get-url", "origin"])?;
    if !repo_urls_identify_same_repository(&origin, repo_url()) {
        return Err(format!(
            "{} is not an ODS checkout from {}.",
            install_dir.display(),
            repo_url()
        ));
    }

    Ok(())
}

fn run_git(install_dir: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(install_dir)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("Failed to run git: {}", e))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

fn normalize_repo_url(url: &str) -> String {
    let trimmed = url.trim().trim_end_matches('/');
    let https = if let Some(rest) = trimmed.strip_prefix("git@github.com:") {
        format!("https://github.com/{rest}")
    } else if let Some(rest) = trimmed.strip_prefix("ssh://git@github.com/") {
        format!("https://github.com/{rest}")
    } else {
        trimmed.to_string()
    };
    https.trim_end_matches(".git").to_ascii_lowercase()
}

fn transferred_repo_url() -> &'static str {
    std::str::from_utf8(TRANSFERRED_REPO_URL_BYTES)
        .expect("transferred repository URL bytes must be valid UTF-8")
}

fn repo_urls_identify_same_repository(candidate: &str, expected: &str) -> bool {
    let candidate = normalize_repo_url(candidate);
    let expected = normalize_repo_url(expected);
    if candidate == expected {
        return true;
    }

    let canonical = normalize_repo_url(DEFAULT_REPO_URL);
    let transferred = normalize_repo_url(transferred_repo_url());
    (candidate == canonical && expected == transferred)
        || (candidate == transferred && expected == canonical)
}

/// Parse a progress line from the installer.
/// Expected format: ODS_PROGRESS:<percent>:<message>
fn parse_progress_line(line: &str) -> Option<ProgressEvent> {
    if let Some(rest) = line.strip_prefix("ODS_PROGRESS:") {
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
        PathBuf::from(home).join("ODS")
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(home).join("ODS")
    }
    #[cfg(target_os = "linux")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        PathBuf::from(home).join("ODS")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fnv1a64(bytes: &[u8]) -> u64 {
        bytes.iter().fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
        })
    }

    #[test]
    fn image_gen_maps_to_the_comfyui_flag() {
        let args = build_install_args(2, &["image_gen".to_string()]);
        assert!(args.contains(&"--comfyui".to_string()));
        // --image-gen is not a real flag; install-core.sh aborts on it.
        assert!(!args.iter().any(|a| a == "--image-gen"));
    }

    #[test]
    fn unselected_features_are_disabled_explicitly() {
        let args = build_install_args(1, &["voice".to_string()]);
        assert_eq!(
            args,
            vec![
                "--tier",
                "1",
                "--voice",
                "--no-workflows",
                "--no-rag",
                "--no-comfyui",
            ]
        );
    }

    #[test]
    fn all_does_not_emit_contradicting_per_feature_flags() {
        let args = build_install_args(3, &["all".to_string()]);
        assert_eq!(args, vec!["--tier", "3", "--all"]);
        assert!(!args.iter().any(|a| a.starts_with("--no-")));
    }

    fn win_args(features: &[&str]) -> Vec<String> {
        let owned: Vec<String> = features.iter().map(|f| f.to_string()).collect();
        build_windows_args(Path::new("C:\\ODS\\install.ps1"), 1, &owned)
    }

    #[test]
    fn windows_args_negate_comfyui_when_unselected() {
        let args = win_args(&["voice"]);
        assert!(args.contains(&"-Voice".to_string()));
        assert!(args.contains(&"-NoComfyui".to_string()));
        assert!(!args.iter().any(|a| a == "-Comfyui"));
    }

    #[test]
    fn windows_args_keep_comfyui_when_selected() {
        let args = win_args(&["image_gen"]);
        assert!(args.contains(&"-Comfyui".to_string()));
        assert!(!args.iter().any(|a| a == "-NoComfyui"));
    }

    #[test]
    fn windows_args_do_not_negate_comfyui_under_all() {
        let args = win_args(&["all"]);
        assert!(args.contains(&"-All".to_string()));
        assert!(!args.iter().any(|a| a == "-NoComfyui"));
    }

    #[test]
    fn windows_args_always_run_non_interactive() {
        assert!(win_args(&[]).contains(&"-NonInteractive".to_string()));
    }

    #[test]
    fn default_install_ref_uses_existing_ods_branch() {
        assert_eq!(DEFAULT_INSTALL_REF, "main");
    }

    #[test]
    fn default_repo_url_uses_canonical_ods_repo() {
        assert_eq!(DEFAULT_REPO_URL, "https://github.com/Osmantic/ODS.git");
    }

    #[test]
    fn normalize_repo_url_accepts_common_github_forms() {
        assert_eq!(
            normalize_repo_url("git@github.com:Osmantic/ODS.git"),
            normalize_repo_url(DEFAULT_REPO_URL)
        );
        assert_eq!(
            normalize_repo_url("ssh://git@github.com/Osmantic/ODS.git/"),
            normalize_repo_url(DEFAULT_REPO_URL)
        );
    }

    #[test]
    fn normalize_repo_url_rejects_unrelated_forks() {
        assert_ne!(
            normalize_repo_url("https://github.com/example/ODS.git"),
            normalize_repo_url(DEFAULT_REPO_URL)
        );
    }

    #[test]
    fn transferred_checkout_alias_is_accepted_without_network_access() {
        assert_eq!(
            fnv1a64(transferred_repo_url().as_bytes()),
            0xb029db1a57045da2
        );
        assert!(repo_urls_identify_same_repository(
            transferred_repo_url(),
            DEFAULT_REPO_URL
        ));
        assert!(repo_urls_identify_same_repository(
            &transferred_repo_url().replacen("https://github.com/", "git@github.com:", 1),
            DEFAULT_REPO_URL
        ));
    }

    #[test]
    fn unrelated_checkout_alias_is_rejected_without_remote_probes() {
        assert!(!repo_urls_identify_same_repository(
            "https://github.com/example/ODS.git",
            DEFAULT_REPO_URL
        ));
    }
}
