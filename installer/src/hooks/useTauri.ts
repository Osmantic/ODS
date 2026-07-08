import { invoke } from "@tauri-apps/api/core";

// Types matching Rust structs

export interface SystemInfo {
  os: string;
  os_version: string;
  arch: string;
  ram_gb: number;
  disk_free_gb: number;
  hostname: string;
  wsl2_available: boolean | null;
  wsl2_installed: boolean | null;
}

export interface RequirementCheck {
  name: string;
  met: boolean;
  found: string;
  required: string;
  help: string | null;
}

export interface DockerStatus {
  installed: boolean;
  running: boolean;
  version: string | null;
  compose_installed: boolean;
  compose_version: string | null;
}

export interface SystemCheckResult {
  system: SystemInfo;
  requirements: RequirementCheck[];
  docker: DockerStatus;
}

export interface PrerequisiteStatus {
  git_installed: boolean;
  docker_installed: boolean;
  docker_running: boolean;
  wsl2_needed: boolean;
  wsl2_installed: boolean;
  all_met: boolean;
}

export interface InstallPrereqResult {
  success: boolean;
  message: string;
  reboot_required: boolean;
}

export interface GpuInfo {
  vendor: "nvidia" | "amd" | "intel" | "apple" | "none";
  name: string;
  vram_mb: number;
  driver_version: string | null;
}

export interface GpuResult {
  gpu: GpuInfo;
  recommended_tier: number;
  tier_description: string;
}

export interface ProgressInfo {
  phase: string;
  percent: number;
  message: string;
  error: string | null;
}

export interface InstallState {
  phase: string;
  install_dir: string | null;
  detected_gpu: GpuInfo | null;
  selected_tier: number | null;
  selected_features: string[];
  error: string | null;
  progress_pct: number;
  progress_message: string;
  reboot_pending: boolean;
}

// Tauri command wrappers

export const checkSystem = () => invoke<SystemCheckResult>("check_system");

export const checkPrerequisites = () =>
  invoke<PrerequisiteStatus>("check_prerequisites");

export const installPrerequisite = (component: string) =>
  invoke<InstallPrereqResult>("install_prerequisites", { component });

export const detectGpu = () => invoke<GpuResult>("detect_gpu");

export const startInstall = (
  tier: number,
  features: string[],
  installDir?: string,
) =>
  invoke<string>("start_install", {
    tier,
    features,
    installDir: installDir ?? null,
  });

export const getInstallProgress = () =>
  invoke<ProgressInfo>("get_install_progress");

export const getInstallState = () =>
  invoke<InstallState>("get_install_state");

export const openODSserver = () => invoke("open_ods");
