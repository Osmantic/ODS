#!/usr/bin/env bash
# ============================================================================
# ODS — P2P GPU Environment Helpers
# ============================================================================
# Part of: ods/installers/p2p-gpu/lib/
# Purpose: .env management, port checks, directory discovery, CPU capping,
#          ownership fixes, HTTP polling, GPU detection, post-install orchestrator
#
# Expects: ODS_USER, ODS_HOME, LOGFILE, log(), warn(), err()
# Provides: env_set(), env_get(), port_in_use(), find_dream_dir(),
#           ensure_ods_cli_command(),
#           cap_cpu_in_yaml(), cap_cpu_in_files(), get_compose_cpu_ceiling(),
#           compute_safe_cpu_cap(), fix_ownership(), wait_for_http(),
#           detect_gpu(), _cap_context_for_vram(), apply_post_install_fixes()
#
# Modder notes:
#   env_set is idempotent — safe to call multiple times with same key.
#   env_set creates .env with 0660 mode to protect secrets and allow dream user access.
#   find_dream_dir checks both expected ODS install paths.
#   detect_gpu() is the single source of truth for GPU detection —
#   call it once and reuse the result (avoid duplicate detection).
#
# SPDX-License-Identifier: Apache-2.0
# ============================================================================

set -euo pipefail

# ── [FIX: env-perms] .env management with proper file permissions ───────────

# Set a key in .env idempotently (no duplicates, preserves inode)
# Creates with 0660 to protect secrets (WEBUI_SECRET, API keys, etc.) and allow dream user
env_set() {
  local file="$1" key="$2" value="$3"
  if [[ ! -f "$file" ]]; then
    install -m 0660 -o "${ODS_USER:-root}" -g "${ODS_USER:-root}" /dev/null "$file"
  fi
  if grep -q "^${key}=" "$file"; then
    # Escape sed delimiter in value to prevent breakage
    local escaped_value="${value//|/\\|}"
    sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

# Read a key from .env
env_get() {
  local file="$1" key="$2"
  [[ ! -f "$file" ]] && return 0
  grep "^${key}=" "$file" 2>>"$LOGFILE" | head -1 | cut -d= -f2- \
    | sed 's/[[:space:]]#.*$//' | tr -d '"' | tr -d "'" || echo ""
}

# Check if a TCP port is in use
port_in_use() {
  local port="$1"
  ss -tlnp 2>&1 | grep -q ":${port} "
}

# Locate the active ods working directory
find_dream_dir() {
  local candidate
  # Prefer directory with both .env and compose (fully configured)
  for candidate in "${ODS_HOME}/ods" "${ODS_HOME}/ODS/ods"; do
    if [[ -f "${candidate}/.env" && -f "${candidate}/docker-compose.base.yml" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  # Fallback: any existing directory (partially configured)
  for candidate in "${ODS_HOME}/ods" "${ODS_HOME}/ODS/ods"; do
    if [[ -d "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# Install a stable `dream` command wrapper for root/non-root shells.
ensure_ods_cli_command() {
  local ds_dir="$1"
  local cli_path="${ds_dir}/ods-cli"
  local wrapper="/usr/local/bin/dream"

  if [[ ! -x "$cli_path" ]]; then
    warn "ods-cli not executable at ${cli_path} (skipping global dream command)"
    return 0
  fi

  cat > "$wrapper" << EOF
#!/usr/bin/env bash
set -euo pipefail
export ODS_HOME="\${ODS_HOME:-${ds_dir}}"
cd "${ds_dir}" || exit 1
exec "${cli_path}" "\$@"
EOF
  # [NON-FATAL: convenience] Missing wrapper only affects global dream alias.
  chmod +x "$wrapper" || warn "chmod failed on ${wrapper} (non-fatal)"
  log "Installed global dream command: ${wrapper}"
}

# Cap CPU values in one YAML file to max_cpu.
# Handles any numeric form (N, N.M) with optional quotes. Values <= max_cpu
# are left alone; values > max_cpu are lowered to max_cpu.
_cap_cpu_in_yaml_file() {
  local file="$1" max_cpu="$2"
  [[ ! -f "$file" ]] && return 0
  python3 - "$file" "$max_cpu" <<'PY'
import re, sys
path, cap = sys.argv[1], float(sys.argv[2])
try:
  with open(path, "r", encoding="utf-8") as fh:
    src = fh.read()
except OSError:
  sys.exit(0)

def parse_numeric(value):
  raw = value.strip().strip("'\"")
  if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
    return float(raw)
  m = re.fullmatch(r"\$\{[^:}]+:-([0-9]+(?:\.[0-9]+)?)\}", raw)
  if m:
    return float(m.group(1))
  return None

def repl(m):
  indent, rhs, comment = m.group(1), m.group(2).strip(), m.group(3) or ""
  q = "'"
  if rhs[:1] in ("'", '"'):
    q = rhs[0]

  numeric = parse_numeric(rhs)
  needs_cap = ("${" in rhs) or (numeric is None) or (numeric > cap)
  if needs_cap:
    return f"{indent}cpus: {q}{cap:g}{q}{comment}"
  return m.group(0)

pat = re.compile(r"^(\s*)cpus:\s*([^#\n]+?)(\s+#.*)?$", re.M)
new = pat.sub(repl, src)
if new != src:
  with open(path, "w", encoding="utf-8") as fh:
    fh.write(new)
PY
}

# Cap CPU values in all YAML files under a directory tree.
cap_cpu_in_yaml() {
  local dir="$1" max_cpu="$2"
  while IFS= read -r -d '' f; do
    _cap_cpu_in_yaml_file "$f" "$max_cpu"
  done < <(find "$dir" \( -name "*.yml" -o -name "*.yaml" \) -type f -print0)
  return 0
}

# Cap CPU values in a specific list of YAML files.
cap_cpu_in_files() {
  local max_cpu="$1"
  shift
  local f
  for f in "$@"; do
    _cap_cpu_in_yaml_file "$f" "$max_cpu"
  done
  return 0
}

# Return the CPU ceiling Docker can actually schedule, accounting for
# container-level CPU quotas that can differ from nproc.
get_compose_cpu_ceiling() {
  local host_nproc docker_ncpu ceiling

  host_nproc=$(nproc 2>>"$LOGFILE" || echo 1)
  if [[ ! "$host_nproc" =~ ^[0-9]+$ ]] || [[ "$host_nproc" -lt 1 ]]; then
    host_nproc=1
  fi

  ceiling="$host_nproc"
  docker_ncpu=$(docker info --format '{{.NCPU}}' 2>>"$LOGFILE" || echo "")
  if [[ "$docker_ncpu" =~ ^[0-9]+$ ]] && [[ "$docker_ncpu" -gt 0 ]] && [[ "$docker_ncpu" -lt "$ceiling" ]]; then
    ceiling="$docker_ncpu"
  fi

  echo "$ceiling"
}

# Compute a safe cpus: cap value with one-core headroom.
# Optional arg 1: hard ceiling discovered from daemon error output.
compute_safe_cpu_cap() {
  local forced_ceiling="${1:-}"
  local ceiling

  ceiling=$(get_compose_cpu_ceiling)
  if [[ "$forced_ceiling" =~ ^[0-9]+$ ]] && [[ "$forced_ceiling" -gt 0 ]] && [[ "$forced_ceiling" -lt "$ceiling" ]]; then
    ceiling="$forced_ceiling"
  fi

  if [[ "$ceiling" -gt 1 ]]; then
    echo $((ceiling - 1))
  else
    echo 1
  fi
}

# Fix ownership recursively (unconditional to catch nested root-owned files)
fix_ownership() {
  local dir="$1" user="$2" group="${3:-$2}"
  [[ ! -d "$dir" ]] && return 0
  # Always apply chown recursively to fix root-owned files inside target-owned directories
  # chown may fail on NFS mounts or in containers without CAP_CHOWN
  if ! chown -R "${user}:${group}" "$dir" 2>>"$LOGFILE"; then
    warn "chown failed on ${dir} (non-fatal; host may block ownership changes)"
  fi
}

# Wait for a URL to return HTTP 200
wait_for_http() {
  local url="$1" timeout="${2:-60}" interval="${3:-5}"
  local elapsed=0
  while [[ $elapsed -lt $timeout ]]; do
    if curl -sf --max-time 5 "$url" > /dev/null 2>&1; then
      return 0
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  return 1
}

# ── [FIX: gpu-dedup] Single source of truth for GPU detection ───────────────
# Sets GPU_BACKEND, GPU_NAME, GPU_VRAM, GPU_COUNT as globals.
# Call once in preflight; all other code reads these variables.
detect_gpu() {
  GPU_BACKEND="cpu"
  GPU_NAME="none"
  GPU_VRAM="0"
  GPU_COUNT=0
  GPU_TOTAL_VRAM=0

  if command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
    GPU_BACKEND="nvidia"
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>>"$LOGFILE" | sed -n '1p' | xargs)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>>"$LOGFILE" | sed -n '1p' | xargs)
    GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>>"$LOGFILE" | wc -l)
    GPU_TOTAL_VRAM=0
    while read -r v; do GPU_TOTAL_VRAM=$(( GPU_TOTAL_VRAM + v )); done \
      < <(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>>"$LOGFILE")
    if [[ $GPU_TOTAL_VRAM -eq 0 ]]; then GPU_TOTAL_VRAM=$GPU_VRAM; fi

  elif command -v rocm-smi &>/dev/null || [[ -e /dev/kfd ]]; then
    GPU_BACKEND="amd"
    GPU_NAME=$(rocm-smi --showproductname 2>>"$LOGFILE" | grep -oP 'Card series:\s*\K.*' | head -1 || echo "AMD GPU")
    GPU_VRAM=$(rocm-smi --showmeminfo vram 2>>"$LOGFILE" | grep -oP 'Total Memory \(B\):\s*\K[0-9]+' | head -1 || echo "0")
    # Convert bytes to MiB
    if [[ "${GPU_VRAM:-0}" -gt 1000000 ]]; then
      GPU_VRAM=$(( GPU_VRAM / 1048576 ))
    fi
    GPU_COUNT=$(rocm-smi --showid 2>>"$LOGFILE" | grep -c 'GPU\[' || echo 1)
    if [[ $GPU_COUNT -ge 2 ]]; then
      GPU_TOTAL_VRAM=$(( GPU_VRAM * GPU_COUNT ))  # rocm-smi per-device sum
    else
      GPU_TOTAL_VRAM=$GPU_VRAM
    fi
  fi

  # Pin packages after successful detection to prevent future mismatches
  if [[ "$GPU_BACKEND" == "nvidia" ]]; then
    _pin_nvidia_packages
  fi
}

# Lightweight backend-only detection (for subcommands that don't need full GPU info)
detect_gpu_backend() {
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    echo "nvidia"
  elif command -v rocm-smi &>/dev/null || [[ -e /dev/kfd ]]; then
    echo "amd"
  else
    echo "cpu"
  fi
}

_has_nvml_mismatch_signature() {
  local output="${1:-}"
  echo "$output" | grep -Eqi \
    "driver/library version mismatch|failed to initialize nvml|nvidia-container-cli: initialization error: nvml error"
}

# ── [FIX: nvml-mismatch] NVIDIA driver/library version mismatch detection ────
# Detects if host NVIDIA driver and container CUDA driver versions are misaligned.
# Returns: 0 = matched, 1 = mismatched, 2 = couldn't detect
# Outputs: diagnostics to stdout (host_driver=X.X container_cuda=Y.Y)
detect_nvml_mismatch() {
  local host_driver container_cuda docker_test_image="${1:-nvidia/cuda:12.4.1-base-ubuntu22.04}"
  local test_timeout="${NVIDIA_DOCKER_TEST_TIMEOUT:-180}"
  local host_probe_output host_probe_rc container_probe_output container_probe_rc

  # Get host driver version
  host_probe_output=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>&1) && host_probe_rc=0 || host_probe_rc=$?
  [[ -n "$host_probe_output" ]] && printf '%s\n' "$host_probe_output" >> "$LOGFILE"

  if [[ $host_probe_rc -eq 0 ]]; then
    host_driver=$(echo "$host_probe_output" | head -1 | xargs || echo "")
  elif _has_nvml_mismatch_signature "$host_probe_output"; then
    log "NVIDIA host probe reported NVML driver/library mismatch"
    return 1
  else
    host_driver=""
  fi

  if [[ -z "$host_driver" ]]; then
    log "NVIDIA driver version detection failed (non-fatal)"
    return 2
  fi

  # Get container CUDA driver compatibility version
  container_probe_output=$(timeout --signal=TERM "$test_timeout" \
    docker run --rm --gpus all "$docker_test_image" \
    nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>&1) && container_probe_rc=0 || container_probe_rc=$?
  [[ -n "$container_probe_output" ]] && printf '%s\n' "$container_probe_output" >> "$LOGFILE"

  if [[ $container_probe_rc -eq 0 ]]; then
    container_cuda=$(echo "$container_probe_output" | head -1 | xargs || echo "")
  elif _has_nvml_mismatch_signature "$container_probe_output"; then
    log "NVIDIA container probe reported NVML driver/library mismatch"
    return 1
  else
    container_cuda=""
  fi

  if [[ -z "$container_cuda" ]]; then
    log "Container CUDA driver detection failed (non-fatal)"
    return 2
  fi

  # Compare major.minor versions (e.g., 535.104.05 → 535.104)
  local host_major_minor container_major_minor
  host_major_minor=$(echo "$host_driver" | cut -d. -f1,2)
  container_major_minor=$(echo "$container_cuda" | cut -d. -f1,2)

  log "NVIDIA driver mismatch check: host=${host_driver} (${host_major_minor}) vs container=${container_cuda} (${container_major_minor})"

  if [[ "$host_major_minor" != "$container_major_minor" ]]; then
    log "NVIDIA driver/library MISMATCH detected: host ${host_driver} != container ${container_cuda}"
    return 1
  fi

  log "NVIDIA driver/library versions aligned (${host_major_minor})"
  return 0
}

# ── [FIX: nvml-mismatch] Multi-strategy NVIDIA driver/library mismatch repair ──
# Strategy 1: Reload kernel modules (fastest, no packages needed)
# Strategy 2: Downgrade userspace libs to match kernel module version
# Strategy 3: Upgrade kernel module to match userspace libs (original approach)
# Non-fatal: logs warnings on failure but does not halt.
repair_nvml_mismatch() {
  local host_probe_output kernel_version="" lib_version="" initial_status post_repair_status

  log "Attempting to repair NVIDIA driver/library mismatch..."

  detect_nvml_mismatch && initial_status=0 || initial_status=$?
  if [[ $initial_status -eq 0 ]]; then
    log "No mismatch detected, skipping repair"
    return 0
  elif [[ $initial_status -eq 2 ]]; then
    # [NON-FATAL: probe] NVML probe may fail on transient driver issues.
    host_probe_output=$(nvidia-smi 2>&1) || warn "nvidia-smi probe failed (non-fatal)"
    if _has_nvml_mismatch_signature "$host_probe_output"; then
      warn "NVIDIA host probe reports driver/library mismatch — forcing repair attempt"
    else
      warn "Unable to detect NVIDIA driver/library mismatch state (skipping repair)"
      return 1
    fi
  fi

  # Get kernel module version (the version that's actually loaded)
  if [[ -f /proc/driver/nvidia/version ]]; then
    kernel_version="$(grep -oP 'Kernel Module\s+\K[0-9.]+' /proc/driver/nvidia/version || echo "")"
  fi
  if [[ -z "${kernel_version:-}" ]] && [[ -f /sys/module/nvidia/version ]]; then
    kernel_version="$(cat /sys/module/nvidia/version 2>/dev/null || echo "")"  # stderr expected: file may not exist
  fi

  # Get NVML library version from nvidia-smi error output
  lib_version="$(nvidia-smi 2>&1 | grep -oP 'NVML library version:\s*\K[0-9.]+' || echo "")"

  if [[ -n "$kernel_version" ]]; then
    log "Kernel module version: ${kernel_version}"
  fi
  if [[ -n "$lib_version" ]]; then
    log "NVML library version: ${lib_version}"
  fi

  # ── Strategy 1: Kernel module reload ────────────────────────────────────
  # Unload and reload NVIDIA modules so the userspace libs match what loads.
  # This is the fastest fix and requires no package changes.
  log "Strategy 1: Attempting kernel module reload..."

  # Stop processes using the GPU before module unload
  local gpu_containers
  gpu_containers="$(docker ps --format '{{.Names}}' --filter 'label=com.docker.compose.project' 2>/dev/null | grep '^ods-' || echo "")"  # stderr expected: docker may not be running
  if [[ -n "$gpu_containers" ]]; then
    log "Stopping Docker containers before module reload..."
    # [NON-FATAL: cleanup] Some containers may already be stopped or unresponsive.
    docker stop $gpu_containers >> "$LOGFILE" 2>&1 || warn "Some containers failed to stop (non-fatal)"
  fi

  # Stop persistence daemon if running
  if pgrep -x nvidia-persistenced >/dev/null 2>&1; then  # stderr expected: process check
    log "Stopping nvidia-persistenced..."
    # [NON-FATAL: cleanup] Persistence daemon may have already exited.
    kill "$(pgrep -x nvidia-persistenced)" 2>/dev/null || warn "nvidia-persistenced not running (non-fatal)"  # stderr expected: may not exist
    sleep 1
  fi

  # Kill any remaining GPU processes
  if [[ -e /dev/nvidia0 ]]; then
    local gpu_pids
    gpu_pids="$(fuser /dev/nvidia* 2>/dev/null | xargs || echo "")"  # stderr expected: fuser probe
    if [[ -n "$gpu_pids" ]]; then
      log "Killing GPU processes: ${gpu_pids}"
      # [NON-FATAL: cleanup] Some GPU processes may have already exited.
      kill $gpu_pids 2>/dev/null || warn "some GPU processes already exited (non-fatal)"  # stderr expected: processes may have exited
      sleep 2
    fi
  fi

  # Unload modules in dependency order
  local reload_success=false
  # [NON-FATAL: cleanup] Module may not be loaded on this host.
  rmmod nvidia_uvm 2>>"$LOGFILE" || warn "nvidia_uvm not loaded (non-fatal)"
  # [NON-FATAL: cleanup] Module may not be loaded on this host.
  rmmod nvidia_drm 2>>"$LOGFILE" || warn "nvidia_drm not loaded (non-fatal)"
  # [NON-FATAL: cleanup] Module may not be loaded on this host.
  rmmod nvidia_modeset 2>>"$LOGFILE" || warn "nvidia_modeset not loaded (non-fatal)"
  if rmmod nvidia 2>>"$LOGFILE"; then
    log "NVIDIA kernel modules unloaded successfully"
    # Reload — nvidia-smi triggers automatic module load
    sleep 1
    if nvidia-smi &>/dev/null; then  # stderr expected: driver reinit
      reload_success=true
      log "NVIDIA kernel modules reloaded — nvidia-smi works"
      nvidia-smi --query-gpu=driver_version,name --format=csv,noheader 2>>"$LOGFILE" | \
        while read -r line; do log "  GPU: ${line}"; done
    else
      warn "nvidia-smi still fails after module reload"
    fi
  else
    warn "Could not unload nvidia module (in use) — trying strategy 2"
  fi

  if [[ "$reload_success" == "true" ]]; then
    # Verify with DKMS that module version matches kernel expectation
    if command -v dkms &>/dev/null; then  # stderr expected: dkms check
      local dkms_status
      dkms_status="$(dkms status 2>/dev/null | grep nvidia || echo "")"  # stderr expected: dkms probe
      if [[ -n "$dkms_status" ]]; then
        log "DKMS status: ${dkms_status}"
      fi
    fi

    # Restart Docker so it picks up the reloaded driver
    # [NON-FATAL: docker] Docker may not be managed by systemctl on Vast.ai.
    # [NON-FATAL: docker] Docker may not be managed by systemctl on Vast.ai.
    systemctl restart docker 2>>"$LOGFILE" || service docker restart 2>>"$LOGFILE" \
      || warn "Docker restart failed (non-fatal)"

    # Verify CUDA compat libs aren't shadowing host driver inside containers
    # (per NVIDIA NIM troubleshooting guide — bundled compat libs at
    #  /usr/local/cuda-*/compat/ can override the host-mounted driver)
    # [NON-FATAL: nvidia-ctk] Toolkit may already be configured or unavailable.
    nvidia-ctk runtime configure --runtime=docker 2>>"$LOGFILE" \
      || warn "nvidia-ctk configure failed (non-fatal)"

    # Re-start any containers we stopped
    if [[ -n "$gpu_containers" ]]; then
      # [NON-FATAL: cleanup] Some containers may fail to restart on driver changes.
      docker start $gpu_containers >> "$LOGFILE" 2>&1 || warn "Some containers failed to restart (non-fatal)"
    fi

    detect_nvml_mismatch && post_repair_status=0 || post_repair_status=$?
    if [[ $post_repair_status -eq 0 ]]; then
      _pin_nvidia_packages
      return 0
    elif [[ $post_repair_status -eq 1 ]]; then
      warn "NVIDIA driver mismatch persists after module reload"
    else
      warn "Unable to verify NVIDIA driver/library mismatch after module reload"
    fi
  fi

  # ── Strategy 2: Downgrade userspace to match kernel module ──────────────
  # If we know the kernel module version, install matching userspace packages.
  if [[ -n "${kernel_version:-}" ]]; then
    log "Strategy 2: Aligning userspace libs to kernel module version ${kernel_version}..."
    local driver_major
    driver_major="$(echo "$kernel_version" | cut -d. -f1)"

    if type -t _wait_for_dpkg_lock >/dev/null 2>&1; then
      # [NON-FATAL: dpkg] apt will still enforce DPkg::Lock::Timeout.
      _wait_for_dpkg_lock 60 || warn "dpkg lock not released in time — DPkg::Lock::Timeout will handle"
    fi

    # Try to install the exact matching driver version
    if apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" update -qq 2>>"$LOGFILE" \
      && apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" install -y -qq \
        --allow-downgrades \
        "nvidia-utils-${driver_major}=${kernel_version}-*" \
        "libnvidia-ml-dev=${kernel_version}-*" \
        2>>"$LOGFILE"; then
      log "Userspace libs downgraded to match kernel ${kernel_version}"
      if nvidia-smi &>/dev/null; then  # stderr expected: driver reinit
        log "nvidia-smi works after userspace downgrade"
        detect_nvml_mismatch && post_repair_status=0 || post_repair_status=$?
        if [[ $post_repair_status -eq 0 ]]; then
          _pin_nvidia_packages
          return 0
        elif [[ $post_repair_status -eq 1 ]]; then
          warn "NVIDIA driver mismatch persists after userspace downgrade"
        else
          warn "Unable to verify NVIDIA driver/library mismatch after userspace downgrade"
        fi
      fi
    else
      warn "Userspace downgrade to ${kernel_version} failed — trying strategy 3"
    fi
  fi

  # ── Strategy 3: Upgrade everything (original approach) ──────────────────
  log "Strategy 3: Attempting full driver upgrade..."
  if type -t _wait_for_dpkg_lock >/dev/null 2>&1; then
    # [NON-FATAL: dpkg] apt will still enforce DPkg::Lock::Timeout.
    _wait_for_dpkg_lock 60 || warn "dpkg lock not released in time — DPkg::Lock::Timeout will handle"
  fi

  if apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" update -qq 2>>"$LOGFILE" \
    && apt-get -o DPkg::Lock::Timeout="${APT_LOCK_TIMEOUT:-120}" install -y -qq \
      --only-upgrade "nvidia-driver-*" 2>>"$LOGFILE"; then
    log "NVIDIA driver upgrade completed"
    systemctl restart docker 2>>"$LOGFILE" || service docker restart 2>>"$LOGFILE" \
      || warn "Docker restart failed (non-fatal)"
    sleep 2
    if nvidia-smi &>/dev/null; then  # stderr expected: driver reinit
      detect_nvml_mismatch && post_repair_status=0 || post_repair_status=$?
      if [[ $post_repair_status -eq 0 ]]; then
        log "NVIDIA driver mismatch RESOLVED after upgrade"
        _pin_nvidia_packages
        return 0
      elif [[ $post_repair_status -eq 1 ]]; then
        warn "NVIDIA driver mismatch persists after upgrade"
      else
        warn "Unable to verify NVIDIA driver/library mismatch after upgrade"
      fi
    else
      warn "nvidia-smi still fails after upgrade"
    fi
  else
    warn "NVIDIA driver upgrade failed"
  fi

  warn "All NVML mismatch repair strategies exhausted — GPU may not work"
  warn "Manual fix: reboot the instance, or try: rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia && nvidia-smi"
  return 1
}

# Pin NVIDIA packages to prevent unattended-upgrades from causing future mismatches
# (NVIDIA support stats: driver mismatches cause 31% of GPU cluster issues)
_pin_nvidia_packages() {
  # Hold nvidia packages so unattended-upgrades can't break them
  local held=0
  for pkg in $(dpkg -l | grep -E '^ii\s+(nvidia-driver|nvidia-utils|nvidia-dkms|libnvidia)' | awk '{print $2}'); do
    apt-mark hold "$pkg" 2>>"$LOGFILE" && held=$((held + 1))
  done
  if [[ $held -gt 0 ]]; then
    log "Pinned ${held} NVIDIA packages (prevents unattended-upgrades mismatch)"
  fi

  # Also blacklist nvidia from unattended-upgrades if config exists
  local uu_conf="/etc/apt/apt.conf.d/50unattended-upgrades"
  if [[ -f "$uu_conf" ]] && ! grep -q 'nvidia' "$uu_conf"; then
    if grep -q 'Unattended-Upgrade::Package-Blacklist' "$uu_conf"; then
      # [NON-FATAL: apt] Blacklist update is best-effort; mismatches are handled elsewhere.
      sed -i '/Unattended-Upgrade::Package-Blacklist/a\    "nvidia-*";' "$uu_conf" 2>>"$LOGFILE" \
        || warn "Failed to add nvidia to unattended-upgrades blacklist (non-fatal)"
      log "Added nvidia-* to unattended-upgrades blacklist"
    fi
  fi
}

# ── Post-install fix orchestrator ───────────────────────────────────────────
# Called by phases/05, subcommands/fix, subcommands/resume.
# Coordinates all post-install fixes in correct order.
apply_post_install_fixes() {
  local ds_dir="$1"
  local gpu_backend="${2:-auto}"
  local data_dir="${ds_dir}/data"
  local env_file="${ds_dir}/.env"
  local cpu_count docker_cpu compose_ceiling max_cpu
  cpu_count=$(nproc 2>>"$LOGFILE" || echo 1)
  docker_cpu=$(docker info --format '{{.NCPU}}' 2>>"$LOGFILE" || echo "unknown")

  [[ "$gpu_backend" == "auto" ]] && gpu_backend=$(detect_gpu_backend)

  # Docker group membership
  if getent group docker &>/dev/null; then
    # [NON-FATAL: permissions] User can still run with sudo or log in again.
    usermod -aG docker "$ODS_USER" || warn "docker group add failed (non-fatal)"
  fi

  # CPU limit fix — cap any cpus: value that exceeds (nproc - 1).
  # Always run: cheap no-op on files whose values already fit.
  compose_ceiling=$(get_compose_cpu_ceiling)
  max_cpu=$(compute_safe_cpu_cap)
  cap_cpu_in_yaml "$ds_dir" "$max_cpu"
  log "CPU limits capped to ${max_cpu} (nproc=${cpu_count}, docker=${docker_cpu}, ceiling=${compose_ceiling})"

  # Keep env-substituted CPU limits safe for overlays that use
  # ${LLAMA_CPU_LIMIT:-...} syntax.
  if [[ -f "$env_file" ]]; then
    local llama_limit="${max_cpu}.0"
    local llama_reservation="2.0"
    if [[ "$max_cpu" -lt 2 ]]; then
      llama_reservation="1.0"
    fi
    env_set "$env_file" "LLAMA_CPU_LIMIT" "$llama_limit"
    env_set "$env_file" "LLAMA_CPU_RESERVATION" "$llama_reservation"
    log "LLAMA CPU env caps set to limit=${llama_limit}, reservation=${llama_reservation}"
  fi

  _apply_permission_fixes "$ds_dir" "$data_dir" "$gpu_backend"
  _apply_compatibility_fixes "$ds_dir"
  _apply_env_defaults "$ds_dir" "$env_file" "$data_dir"
  ensure_ods_cli_command "$ds_dir"
  _cap_context_for_vram "$ds_dir"

  # ── [FIX: nvml-mismatch] Post-install NVIDIA driver check (fallback) ──────
  if [[ "$gpu_backend" == "nvidia" ]]; then
    log "Checking for NVIDIA driver/library version alignment (post-install)..."
    if detect_nvml_mismatch; then
      :
    else
      mismatch_status=$?
      if [[ $mismatch_status -eq 1 ]]; then
        warn "NVIDIA driver/library mismatch detected post-install (non-fatal)"
        warn "Run 'bash setup.sh --fix' to repair, or manually upgrade nvidia-driver-*"
      elif [[ $mismatch_status -eq 2 ]]; then
        local host_probe_output
        # [NON-FATAL: probe] NVML probe may fail on transient driver issues.
        host_probe_output=$(nvidia-smi 2>&1) || warn "nvidia-smi probe failed (non-fatal)"
        if _has_nvml_mismatch_signature "$host_probe_output"; then
          warn "Host NVIDIA stack reports driver/library mismatch (non-fatal)"
          warn "If 'bash setup.sh --fix' cannot recover, reinstall NVIDIA driver package and reboot"
        fi
      fi
    fi
  fi

  log "Post-install fixes applied (including ACL-based permission system)"
}

_apply_permission_fixes() {
  local ds_dir="$1" data_dir="$2" gpu_backend="$3"
  ensure_acl_tools
  precreate_extension_data_dirs "$ds_dir"
  apply_data_acl "$data_dir"
  fix_known_uid_requirements "$data_dir" "$gpu_backend"
  configure_dream_umask
  create_permission_fix_script "$ds_dir"
  apply_data_acl "${ds_dir}/extensions"
  if [[ -d "${ds_dir}/user-extensions" ]]; then
    apply_data_acl "${ds_dir}/user-extensions"
  fi
  # [NON-FATAL: scripts] Missing exec bits only affects helper scripts.
  find "${ds_dir}/scripts" -name "*.sh" -exec chmod +x {} + || warn "chmod scripts failed (non-fatal)"
  mkdir -p "${ds_dir}/logs"
  apply_data_acl "${ds_dir}/logs"
}

_apply_compatibility_fixes() {
  local ds_dir="$1"
  ensure_whisper_ui_compatibility "$ds_dir"
  ensure_webui_stt_model_alignment "$ds_dir"
  patch_openclaw_inject_token_runtime "$ds_dir"
}

_apply_env_defaults() {
  local ds_dir="$1" env_file="$2" data_dir="$3"

  # Seed .env from .env.example if missing (fatal if fails — compose cannot start without all required variables)
  if [[ ! -f "$env_file" ]]; then
    local env_example="${ds_dir}/.env.example"
    if [[ -f "$env_example" ]]; then
      cp "$env_example" "$env_file" || {
        err ".env.example copy to ${env_file} failed — Docker Compose cannot start"
        exit 1
      }
      chown "${ODS_USER}:${ODS_USER}" "$env_file" || {
        err ".env ownership fix after copy failed — Docker Compose cannot start"
        exit 1
      }
      chmod 0660 "$env_file" || {
        err ".env chmod to 0660 after copy failed — Docker Compose cannot start"
        exit 1
      }
      log "Seeded .env from .env.example"
    else
      log "No .env.example found; will create .env via env_set()"
    fi
  fi

  # Fix .env ownership and permissions if file exists (fatal if fails — compose cannot start without readable .env)
  if [[ -f "$env_file" ]]; then
    # Check and fix ownership independently
    if [[ "$(stat -c '%U' "$env_file" 2>>"$LOGFILE" || echo root)" != "${ODS_USER}" ]]; then
      chown "${ODS_USER}:${ODS_USER}" "$env_file" || {
        err ".env ownership fix failed — Docker Compose cannot start"
        exit 1
      }
    fi
    # Check and fix mode independently
    if [[ "$(stat -c '%a' "$env_file" 2>>"$LOGFILE")" != "660" ]]; then
      chmod 0660 "$env_file" || {
        err ".env chmod to 0660 failed — Docker Compose cannot start"
        exit 1
      }
    fi
  fi

  # Helper: Replace CHANGEME or empty with generated secret/value
  _replace_changeme() {
    local key="$1" value="$2"
    local current="$(env_get "$env_file" "$key")"
    if [[ -z "$current" || "$current" == "CHANGEME" ]]; then
      env_set "$env_file" "$key" "$value"
      log "Set ${key}"
    fi
  }

  # Generate or replace hard-required secrets (compose uses ${VAR:?error} syntax)
  _replace_changeme "WEBUI_SECRET" "$(openssl rand -hex 32)"
  _replace_changeme "SEARXNG_SECRET" "$(openssl rand -hex 32)"
  _replace_changeme "LITELLM_KEY" "sk-ods-$(openssl rand -hex 16)"
  _replace_changeme "N8N_PASS" "$(openssl rand -hex 16)"
  _replace_changeme "LIVEKIT_API_KEY" "$(openssl rand -hex 16)"
  _replace_changeme "LIVEKIT_API_SECRET" "$(openssl rand -hex 32)"
  _replace_changeme "DIFY_SECRET_KEY" "$(openssl rand -hex 32)"
  _replace_changeme "OPENCODE_SERVER_PASSWORD" "$(openssl rand -hex 16)"

  # Set non-secret required variables (also checked by compose)
  _replace_changeme "N8N_USER" "admin@ods.local"
  _replace_changeme "OPENCLAW_TOKEN" "$(openssl rand -hex 24)"
  _replace_changeme "DASHBOARD_API_KEY" "$(openssl rand -hex 24)"

  # GGUF_FILE — detect from data/models if not set
  if [[ -z "$(env_get "$env_file" "GGUF_FILE")" ]]; then
    local first_model
    first_model=$(find "${data_dir}/models/" -maxdepth 1 -name "*.gguf" -type f \
      -printf '%s %f\n' 2>&1 | sort -rn | head -1 | cut -d' ' -f2- || echo "")
    if [[ -n "$first_model" ]]; then
      env_set "$env_file" "GGUF_FILE" "$first_model"
      log "Set GGUF_FILE=${first_model}"
    fi
  fi
}

# ── VRAM-aware context size capping ───────────────────────────────────────
# The upstream installer sets CTX_SIZE=131072 when Hermes is enabled, but
# this exceeds VRAM on cards <=24 GB with large models. Cap CTX_SIZE based
# on available VRAM headroom after model weight, and enable KV cache
# quantization to maximize usable context within the budget.
_cap_context_for_vram() {
  local ds_dir="$1"
  local env_file="${ds_dir}/.env"

  # Skip if no GPU
  if [[ "${GPU_BACKEND:-cpu}" == "cpu" ]]; then
    return 0
  fi

  local vram_mb="${GPU_VRAM:-0}"
  local per_gpu_vram_mb="${GPU_VRAM:-0}"
  local model_size_per_gpu_mb=0
  local current_ctx model_size_mb headroom_mb safe_ctx kv_quant

  # Multi-GPU: cap by per-GPU VRAM budget to avoid CUDA0 OOM
  if [[ "${GPU_COUNT:-1}" -ge 2 && "${GPU_TOTAL_VRAM:-0}" -gt 0 ]]; then
    per_gpu_vram_mb=$(( GPU_TOTAL_VRAM / GPU_COUNT ))
    if [[ "${GPU_VRAMS+set}" == "set" && "${#GPU_VRAMS[@]}" -gt 0 ]]; then
      local min_vram="${GPU_VRAMS[0]}"
      local vram
      for vram in "${GPU_VRAMS[@]}"; do
        if [[ "$vram" -lt "$min_vram" ]]; then
          min_vram="$vram"
        fi
      done
      per_gpu_vram_mb="$min_vram"
    fi
  fi

  current_ctx="$(env_get "$env_file" "CTX_SIZE")"
  current_ctx="${current_ctx:-16384}"

  # Get model size from .env or fallback to TIER_MODEL_SIZE_MB
  model_size_mb="$(env_get "$env_file" "LLM_MODEL_SIZE_MB")"
  model_size_mb="${model_size_mb:-${TIER_MODEL_SIZE_MB:-0}}"

  if [[ "$per_gpu_vram_mb" -eq 0 || "$model_size_mb" -eq 0 ]]; then
    log "VRAM or model size unknown -- skipping context cap"
    return 0
  fi

  # Split model weight across GPUs when available; fall back to full size on single GPU.
  if [[ "${GPU_COUNT:-1}" -ge 2 ]]; then
    model_size_per_gpu_mb=$(( (model_size_mb + GPU_COUNT - 1) / GPU_COUNT ))
  else
    model_size_per_gpu_mb="$model_size_mb"
  fi

  # Calculate per-GPU headroom (VRAM - model weight per GPU - 1 GB overhead)
  headroom_mb=$(( per_gpu_vram_mb - model_size_per_gpu_mb - 1024 ))

  if [[ $headroom_mb -le 0 ]]; then
    # Model barely fits -- use minimum context
    safe_ctx=2048
    kv_quant="q4_0"
    warn "Model (${model_size_mb}MB) nearly exceeds GPU VRAM (${per_gpu_vram_mb}MB) -- setting CTX_SIZE=${safe_ctx}"
  elif [[ $headroom_mb -le 2048 ]]; then
    # ~2 GB headroom
    safe_ctx=4096
    kv_quant="q4_0"
  elif [[ $headroom_mb -le 4096 ]]; then
    # ~4 GB headroom (typical RTX 3090 with 18.6 GB model)
    safe_ctx=16384
    kv_quant="q8_0"
  elif [[ $headroom_mb -le 8192 ]]; then
    # ~8 GB headroom
    safe_ctx=32768
    kv_quant="q8_0"
  elif [[ $headroom_mb -le 16384 ]]; then
    # ~16 GB headroom (e.g., RTX 4090 with smaller model)
    safe_ctx=65536
    kv_quant="q8_0"
  else
    # >16 GB headroom -- large GPU, let it run
    safe_ctx=131072
    kv_quant="f16"
  fi

  if [[ "$current_ctx" -gt "$safe_ctx" ]]; then
    log "VRAM budget per GPU: ${per_gpu_vram_mb}MB, model per GPU: ${model_size_per_gpu_mb}MB, headroom: ${headroom_mb}MB"
    log "Capping CTX_SIZE: ${current_ctx} -> ${safe_ctx} (prevents OOM on ${per_gpu_vram_mb}MB GPU)"
    env_set "$env_file" "CTX_SIZE" "$safe_ctx"

    # Set KV cache quantization to maximize context within VRAM budget
    local current_kv_k current_kv_v
    current_kv_k="$(env_get "$env_file" "LLAMA_ARG_CACHE_TYPE_K")"
    current_kv_v="$(env_get "$env_file" "LLAMA_ARG_CACHE_TYPE_V")"

    if [[ "${current_kv_k:-f16}" == "f16" && "$kv_quant" != "f16" ]]; then
      env_set "$env_file" "LLAMA_ARG_CACHE_TYPE_K" "$kv_quant"
      env_set "$env_file" "LLAMA_ARG_CACHE_TYPE_V" "$kv_quant"
      log "KV cache quantization: f16 -> ${kv_quant} (reduces VRAM, trades some quality)"
    fi
  else
    log "CTX_SIZE=${current_ctx} fits within VRAM budget (${headroom_mb}MB headroom) -- no change"
  fi

  _cap_batch_for_vram "$env_file" "$per_gpu_vram_mb" "$safe_ctx"
}

# ── VRAM-aware batch size capping ─────────────────────────────────────────
# Prevent compute buffer OOM on multi-GPU by bounding batch size per GPU.
_cap_batch_for_vram() {
  local env_file="$1" vram_mb="$2" ctx_size="$3"
  local current_batch safe_batch

  current_batch="$(env_get "$env_file" "LLAMA_BATCH_SIZE")"
  current_batch="${current_batch:-2048}"

  if [[ "$vram_mb" -le 12288 ]]; then
    safe_batch=256
  elif [[ "$vram_mb" -le 16384 ]]; then
    safe_batch=512
  elif [[ "$vram_mb" -le 24576 ]]; then
    safe_batch=1024
  else
    safe_batch=2048
  fi

  if [[ "$ctx_size" -ge 65536 && "$safe_batch" -gt 512 ]]; then
    safe_batch=512
  elif [[ "$ctx_size" -ge 32768 && "$safe_batch" -gt 1024 ]]; then
    safe_batch=1024
  fi

  if [[ ! "$current_batch" =~ ^[0-9]+$ ]]; then
    env_set "$env_file" "LLAMA_BATCH_SIZE" "$safe_batch"
    log "LLAMA_BATCH_SIZE invalid ('${current_batch}') -- set to ${safe_batch}"
    return 0
  fi

  if [[ "$current_batch" -gt "$safe_batch" ]]; then
    env_set "$env_file" "LLAMA_BATCH_SIZE" "$safe_batch"
    log "Capping LLAMA_BATCH_SIZE: ${current_batch} -> ${safe_batch} (prevents CUDA OOM)"
  else
    log "LLAMA_BATCH_SIZE=${current_batch} fits within VRAM budget -- no change"
  fi
}
