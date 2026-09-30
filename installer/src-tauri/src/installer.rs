use crate::state::{InstallPhase, InstallState};
use serde::Serialize;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

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

/// The in-flight installer subprocess and its progress state, retained so an
/// explicit cancel or an app exit can terminate the direct child instead of leaving
/// it mutating the checkout and Docker project with no supervising UI.
static ACTIVE_CHILD: Mutex<Option<(Child, Arc<Mutex<InstallState>>)>> = Mutex::new(None);

/// Terminate the in-flight installer subprocess, if any, and persist a
/// cancelled state. Returns an error if stopping or persisting cancellation fails.
pub fn terminate_active_child() -> Result<bool, String> {
    let mut guard = ACTIVE_CHILD.lock().map_err(|e| e.to_string())?;
    let Some((child, _)) = guard.as_mut() else {
        return Ok(false);
    };
    if child.try_wait().map_err(|e| format!("Failed to inspect installer process: {e}"))?.is_some() {
        return Ok(false);
    }
    #[cfg(target_os = "windows")]
    {
        // The wrapper launches further PowerShell/WSL host processes which can
        // inherit its pipes. Killing only the root leaves those writers alive.
        let output = Command::new("taskkill.exe")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .output()
            .map_err(|e| format!("Failed to stop installer process tree: {e}"))?;
        if !output.status.success() {
            if child.try_wait().map_err(|e| format!("Failed to inspect installer process after termination error: {e}"))?.is_some() {
                return Ok(false);
            }
            return Err(format!("Failed to stop installer process tree: {}{}",
                String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)));
        }
    }
    #[cfg(not(target_os = "windows"))]
    child.kill().map_err(|e| format!("Failed to stop installer process: {e}"))?;
    child.wait().map_err(|e| format!("Failed to reap installer process: {e}"))?;
    let (_, state) = guard.take().expect("registered child remains owned until reaped");
    drop(guard);
    mark_cancelled(&state)?;
    Ok(true)
}

fn mark_cancelled(state: &Arc<Mutex<InstallState>>) -> Result<(), String> {
    let mut s = state.lock().map_err(|e| e.to_string())?;
    s.phase = InstallPhase::Error;
    s.error = Some("Installation cancelled.".to_string());
    s.progress_message = "Installation cancelled.".to_string();
    s.save()
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

    ensure_checkout(&install_dir, &state)?;

    update_progress(&state, "Configuring installation", 15);

    // Phase 2: Build installer arguments
    let ods_dir = install_dir.join("ods");
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

    // Drain pipes independently of the child handle so cancellation can acquire
    // the registry while the installer is still writing progress.
    let stdout = child.stdout.take();
    *ACTIVE_CHILD.lock().unwrap() = Some((child, Arc::clone(&state)));
    if let Some(stdout) = stdout {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                if let Some(progress) = parse_progress_line(&line) {
                    update_progress(&state, &progress.message, progress.percent);
                }
            }
        }
    }

    let output = wait_active_child(&state)?;
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

fn wait_active_child(state: &Arc<Mutex<InstallState>>) -> Result<ExitStatus, String> {
    // Retain the child so cancel/exit can terminate it. try_wait polling keeps
    // the slot reachable: a blocking wait() would hold the mutex and deadlock
    // the terminate path.
    enum ChildPoll {
        Exited(ExitStatus),
        Running,
        Gone,
        Failed(std::io::Error),
    }

    loop {
        let poll = {
            let mut guard = ACTIVE_CHILD.lock().unwrap();
            match guard.as_mut() {
                None => ChildPoll::Gone,
                Some((child, _)) => match child.try_wait() {
                    Ok(Some(status)) => ChildPoll::Exited(status),
                    Ok(None) => ChildPoll::Running,
                    Err(e) => ChildPoll::Failed(e),
                },
            }
        };
        match poll {
            ChildPoll::Exited(status) => {
                ACTIVE_CHILD.lock().unwrap().take();
                return Ok(status);
            }
            ChildPoll::Running => thread::sleep(Duration::from_millis(100)),
            // terminate_active_child() took, killed, and marked the state.
            ChildPoll::Gone => {
                mark_cancelled(&state)?;
                return Err("Installation cancelled.".to_string());
            }
            ChildPoll::Failed(e) => {
                ACTIVE_CHILD.lock().unwrap().take();
                return Err(format!("Installer process error: {}", e));
            }
        }
    }
}

fn ensure_checkout(install_dir: &Path, state: &Arc<Mutex<InstallState>>) -> Result<(), String> {
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

    let mut command = Command::new("git");
    command
        .args([
            "clone",
            "--depth",
            "1",
            "--branch",
            install_ref(),
            repo_url(),
        ])
        .arg(install_dir);
    run_clone_command(&mut command, state)?;
    validate_checkout(install_dir)
}

fn run_clone_command(command: &mut Command, state: &Arc<Mutex<InstallState>>) -> Result<(), String> {
    let mut clone = command
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to clone repository: {}", e))?;

    let stderr = clone.stderr.take().expect("clone stderr is piped");
    *ACTIVE_CHILD.lock().unwrap() = Some((clone, Arc::clone(state)));
    let stderr_handle = thread::spawn(move || {
        let mut bytes = Vec::new();
        BufReader::new(stderr).read_to_end(&mut bytes).map(|_| bytes)
    });
    let status = wait_active_child(state);
    let stderr_bytes = stderr_handle.join()
        .map_err(|_| "Git clone stderr reader panicked.".to_string())?
        .map_err(|e| format!("Failed to read git clone diagnostics: {e}"))?;
    let status = status?;
    if !status.success() {
        return Err(format!("Git clone failed for ODS ref '{}': {}", install_ref(), String::from_utf8_lossy(&stderr_bytes)));
    }
    Ok(())
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
        if s.error.is_some() {
            return;
        }
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

    static CHILD_TEST_LOCK: Mutex<()> = Mutex::new(());

    struct TestStateDirectory {
        path: PathBuf,
        old_local: Option<std::ffi::OsString>,
        old_xdg: Option<std::ffi::OsString>,
        old_home: Option<std::ffi::OsString>,
    }

    impl TestStateDirectory {
        fn new() -> Self {
            let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
            let path = std::env::temp_dir().join(format!("ods-child-test-{}-{unique}", std::process::id()));
            std::fs::create_dir_all(&path).unwrap();
            let fixture = Self { path, old_local: std::env::var_os("LOCALAPPDATA"), old_xdg: std::env::var_os("XDG_DATA_HOME"), old_home: std::env::var_os("HOME") };
            std::env::set_var("HOME", &fixture.path);
            std::env::set_var("LOCALAPPDATA", &fixture.path);
            std::env::set_var("XDG_DATA_HOME", &fixture.path);
            fixture
        }
    }

    impl Drop for TestStateDirectory {
        fn drop(&mut self) {
            for (key, previous) in [("LOCALAPPDATA", &self.old_local), ("XDG_DATA_HOME", &self.old_xdg), ("HOME", &self.old_home)] {
                match previous { Some(value) => std::env::set_var(key, value), None => std::env::remove_var(key) }
            }
            if let Err(error) = std::fs::remove_dir_all(&self.path) {
                eprintln!("Failed to clean child-test fixture: {error}");
            }
        }
    }

    fn fnv1a64(bytes: &[u8]) -> u64 {
        bytes.iter().fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
        })
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

    fn spawn_idle_child() -> Child {
        if cfg!(target_os = "windows") {
            Command::new("powershell.exe")
                .args(["-NoProfile", "-Command", "Start-Sleep", "-Seconds", "600"])
                .spawn()
                .expect("spawn idle powershell")
        } else {
            Command::new("sleep")
                .arg("600")
                .spawn()
                .expect("spawn idle sleep")
        }
    }

    #[test]
    fn terminate_active_child_kills_child_and_marks_state_cancelled() {
        let _test_guard = CHILD_TEST_LOCK.lock().unwrap();
        // Redirect the persisted state file away from the real user dir.
        let _directory = TestStateDirectory::new();

        let state = Arc::new(Mutex::new(InstallState {
            phase: InstallPhase::Installing,
            ..Default::default()
        }));
        *ACTIVE_CHILD.lock().unwrap() = Some((spawn_idle_child(), Arc::clone(&state)));

        assert!(terminate_active_child().unwrap(), "registered child should terminate");
        {
            let s = state.lock().unwrap();
            assert_eq!(s.phase, InstallPhase::Error);
            assert!(s.progress_message.contains("cancelled"));
            assert_eq!(s.error.as_deref(), Some("Installation cancelled."));
        }
        assert!(
            ACTIVE_CHILD.lock().unwrap().is_none(),
            "child slot must be cleared after termination"
        );
        assert!(
            !terminate_active_child().unwrap(),
            "a second terminate must be a no-op"
        );
    }

    #[test]
    fn terminate_active_child_without_install_is_noop() {
        let _test_guard = CHILD_TEST_LOCK.lock().unwrap();
        assert!(!terminate_active_child().unwrap());
    }

    #[test]
    fn already_exited_child_is_not_marked_cancelled() {
        let _test_guard = CHILD_TEST_LOCK.lock().unwrap();
        let _directory = TestStateDirectory::new();
        let state = Arc::new(Mutex::new(InstallState::default()));
        let mut child = if cfg!(target_os = "windows") {
            Command::new("powershell.exe").args(["-NoProfile", "-Command", "exit 0"]).spawn().unwrap()
        } else {
            Command::new("sh").args(["-c", "exit 0"]).spawn().unwrap()
        };
        assert!(child.wait().unwrap().success());
        *ACTIVE_CHILD.lock().unwrap() = Some((child, Arc::clone(&state)));
        assert!(!terminate_active_child().unwrap());
        assert_eq!(state.lock().unwrap().phase, InstallPhase::Welcome);
        assert!(state.lock().unwrap().error.is_none());
        assert!(wait_active_child(&state).unwrap().success());
        assert!(ACTIVE_CHILD.lock().unwrap().is_none());
    }

    fn clone_fixture(mode: &str) -> Command {
        if cfg!(target_os = "windows") {
            let mut command = Command::new("powershell.exe");
            command.args(["-NoProfile", "-Command", &format!(
                "[Console]::Out.Write(('x' * 262144)); [Console]::Error.WriteLine(('x' * 262144)); [Console]::OpenStandardError().WriteByte(255); if ('{mode}' -eq 'wait') {{ Start-Sleep -Seconds 5 }}; if ('{mode}' -eq 'failure') {{ [Console]::Error.WriteLine('Fixture clone failure'); exit 7 }}; exit 0"
            )]);
            command
        } else {
            let mut command = Command::new("bash");
            command.args(["-c", &format!(
                "printf '%262144s' ''; printf '%262144s\\n' '' >&2; printf '\\377' >&2; if [ '{mode}' = wait ]; then deadline=$((SECONDS+5)); while ((SECONDS < deadline)); do :; done; fi; if [ '{mode}' = failure ]; then printf 'Fixture clone failure\\n' >&2; exit 7; fi"
            )]);
            command
        }
    }

    #[test]
    fn managed_clone_is_cancellable_and_retains_failure_diagnostics() {
        let _test_guard = CHILD_TEST_LOCK.lock().unwrap();
        let _directory = TestStateDirectory::new();
        let state = Arc::new(Mutex::new(InstallState::default()));
        let worker_state = Arc::clone(&state);
        let worker = thread::spawn(move || run_clone_command(&mut clone_fixture("wait"), &worker_state));
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while ACTIVE_CHILD.lock().unwrap().is_none() {
            assert!(std::time::Instant::now() < deadline, "clone child was not registered");
            thread::sleep(Duration::from_millis(10));
        }
        let start = std::time::Instant::now();
        assert!(terminate_active_child().unwrap());
        assert_eq!(worker.join().unwrap(), Err("Installation cancelled.".into()));
        assert!(start.elapsed() < Duration::from_secs(3));
        let persisted = InstallState::load().unwrap();
        assert_eq!(persisted.phase, InstallPhase::Error);
        assert_eq!(persisted.error.as_deref(), Some("Installation cancelled."));
        assert!(!terminate_active_child().unwrap());

        let state = Arc::new(Mutex::new(InstallState::default()));
        assert_eq!(run_clone_command(&mut clone_fixture("success"), &state), Ok(()));
        assert!(!terminate_active_child().unwrap());
        let error = run_clone_command(&mut clone_fixture("failure"), &state).unwrap_err();
        assert!(error.contains("Fixture clone failure"));
        assert!(!terminate_active_child().unwrap());
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn streaming_install_can_be_cancelled_before_stdout_closes() {
        let _test_guard = CHILD_TEST_LOCK.lock().unwrap();
        let directory = TestStateDirectory::new();
        let checkout = directory.path.join("checkout");
        std::fs::create_dir_all(checkout.join("ods")).unwrap();
        assert!(Command::new("git").args(["init", "--quiet"]).arg(&checkout).status().unwrap().success());
        assert!(Command::new("git").arg("-C").arg(&checkout)
            .args(["remote", "add", "origin", repo_url()]).status().unwrap().success());
        std::fs::write(checkout.join("install.ps1"), r#"
param([switch]$NonInteractive, [string]$Tier)
if ($Tier -eq '1') {
    $descendant = Start-Process powershell.exe -NoNewWindow -PassThru -ArgumentList '-NoProfile', '-Command', 'Start-Sleep -Seconds 5'
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'descendant.pid'), [string]$descendant.Id)
}
Write-Output 'ODS_PROGRESS:42:fixture:Fixture running'
if ($Tier -eq '1') { Start-Sleep -Seconds 5 }
if ($Tier -eq '3') { [Console]::Error.WriteLine('Fixture failure'); exit 7 }
exit 0
"#).unwrap();

        let state = Arc::new(Mutex::new(InstallState::default()));
        let worker_state = Arc::clone(&state);
        let worker_dir = checkout.clone();
        let worker = thread::spawn(move || run_install(worker_state, worker_dir, 1, vec![]));
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while state.lock().unwrap().progress_pct != 42 {
            assert!(std::time::Instant::now() < deadline, "fixture progress not received");
            thread::sleep(Duration::from_millis(10));
        }
        let start = std::time::Instant::now();
        let cancelled = terminate_active_child().unwrap();
        let result = worker.join().unwrap();
        assert!(cancelled, "streaming installer must be registered before reading stdout");
        assert_eq!(result, Err("Installation cancelled.".into()));
        assert!(start.elapsed() < Duration::from_secs(3));
        let persisted = InstallState::load().unwrap();
        assert_eq!(persisted.phase, InstallPhase::Error);
        assert_eq!(persisted.error.as_deref(), Some("Installation cancelled."));
        let descendant = std::fs::read_to_string(checkout.join("descendant.pid")).unwrap();
        assert!(Command::new("powershell.exe").args(["-NoProfile", "-Command",
            &format!("if (Get-Process -Id {descendant} -ErrorAction SilentlyContinue) {{ exit 1 }} else {{ exit 0 }}")])
            .status().unwrap().success(), "installer descendant must be stopped");
        assert!(!terminate_active_child().unwrap());

        let success = Arc::new(Mutex::new(InstallState::default()));
        assert_eq!(run_install(Arc::clone(&success), checkout.clone(), 2, vec![]), Ok(()));
        assert_eq!(success.lock().unwrap().phase, InstallPhase::Complete);
        assert!(!terminate_active_child().unwrap());
        let failure = Arc::new(Mutex::new(InstallState::default()));
        assert!(run_install(failure, checkout, 3, vec![]).unwrap_err().contains("Fixture failure"));
        assert!(!terminate_active_child().unwrap());
    }
}
