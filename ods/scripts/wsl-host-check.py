#!/usr/bin/env python3
"""What `ods doctor` checks about the Windows host when ODS runs in WSL.

Read-only. Prints one JSON object with the facts and `findings`, in the
doctor's diagnosis shape (id, severity, confidence, title, evidence, impact,
next_steps):

- Windows commit headroom: GlobalMemoryStatusEx ullAvailPageFile, read
  through WSL interop. When it reaches zero Windows fails allocations and can
  crash WSL itself (RTX Spark, 2026-10-25: event 2004, wslservice.exe crashed,
  WSL restarted). A model server on Windows reports its own budget, which is
  not this number.
- The WSL memory limit: memory= in %USERPROFILE%\\.wslconfig against the
  MemTotal this WSL VM really has, which shows whether WSL restarted since.
- Containers that lost their data: ods-dashboard-api's /data must be the
  host's data directory (the identity rule of scripts/wsl-bind-recovery.py).
- Published ports Windows cannot reach while their containers run.
- The host agent's own record of its repair after the last WSL boot.

Every probe has a timeout and a failed probe only leaves its fact unknown.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

GIB = 1024 ** 3
# Below this much commit headroom, app installs and image turns can use up
# the rest and crash WSL (the RTX Spark had 2-5 GiB left when it did).
COMMIT_WARN_GIB = 8
COMMIT_CRITICAL_GIB = 2
# MemTotal sits a little under the configured memory=; this much above it
# means WSL still runs with an older limit.
LIMIT_SLACK_GIB = 0.25
POWERSHELL_TIMEOUT = 45
DOCKER_TIMEOUT = 15
MAX_PORTS = 12
WINDOWS_POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
HELP = "Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC"
DOCKER_RESTART = ("Restart Docker Desktop: in Windows, right-click the Docker icon in the taskbar and choose "
                  "Restart (or run 'docker desktop restart' in PowerShell). ODS repairs its containers by "
                  "itself afterwards; if this still shows, run: ods repair mounts")
WSL_RESTART = ("Restart WSL so the limit applies: save your work in Ubuntu, then restart Windows, or run "
               "'wsl --shutdown' in PowerShell and start Docker Desktop again. ODS repairs its containers by "
               "itself after WSL restarts.")

# Fixed read-only Windows script, never interpolated with untrusted text:
# ports are integers checked below.
POWERSHELL_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if (-not ('OdsMemoryStatus' -as [type])) {
Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class OdsMemoryStatus {
  [StructLayout(LayoutKind.Sequential)] public class Status {
    public uint Length = (uint)Marshal.SizeOf(typeof(Status)); public uint MemoryLoad;
    public ulong TotalPhys, AvailPhys, TotalPageFile, AvailPageFile, TotalVirtual, AvailVirtual, AvailExtendedVirtual;
  }
  [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
  static extern bool GlobalMemoryStatusEx([In, Out] Status status);
  public static Status Read() { var s = new Status(); if (!GlobalMemoryStatusEx(s)) { throw new Win32Exception(); } return s; }
}
"@
}
function Test-OdsPort($Probe) {
  # Windows takes about 2 s to report a refused loopback connection, so the
  # connects all start first and each gets 3 s.
  try {
    if (-not $Probe.Connect.Wait(3000)) { return 'timeout' }
    $stream = $Probe.Client.GetStream()
    $stream.ReadTimeout = 2500
    $request = [Text.Encoding]::ASCII.GetBytes("GET / HTTP/1.1`r`nHost: 127.0.0.1:$($Probe.Port)`r`nConnection: close`r`n`r`n")
    $stream.Write($request, 0, $request.Length)
    $reply = New-Object byte[] 64
    if ($stream.Read($reply, 0, 64) -gt 0) { return 'answering' }
    return 'closed'
  } catch {
    $cause = $_.Exception
    while ($cause -and $cause -isnot [Net.Sockets.SocketException]) { $cause = $cause.InnerException }
    if (-not $cause) { return 'closed' }
    switch ($cause.SocketErrorCode) {
      'ConnectionRefused' { return 'refused' }
      'TimedOut' { return 'timeout' }
      default { return 'closed' }
    }
  } finally { $Probe.Client.Dispose() }
}
$status = [OdsMemoryStatus]::Read()
$config = Join-Path $env:USERPROFILE '.wslconfig'
$text = $null
if (Test-Path -LiteralPath $config -PathType Leaf) { $text = [IO.File]::ReadAllText($config) }
$probes = @()
foreach ($port in @(__PORTS__)) {
  $client = [Net.Sockets.TcpClient]::new()
  $probes += [pscustomobject]@{ Port = $port; Client = $client; Connect = $client.ConnectAsync('127.0.0.1', $port) }
}
$ports = [ordered]@{}
foreach ($probe in $probes) { $ports["$($probe.Port)"] = Test-OdsPort $probe }
[pscustomobject]@{
  totalPhys = $status.TotalPhys; availPhys = $status.AvailPhys
  totalPageFile = $status.TotalPageFile; availPageFile = $status.AvailPageFile
  wslconfig = $text; ports = $ports
} | ConvertTo-Json -Compress -Depth 4
"""


def gib(value):
    return round(value / GIB, 2)


def wsl_memory_value_gib(value):
    """WSL sizes (16GB, 16G, 8192MB, 1TB or bytes; GB means GiB) as GiB, or None."""
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]B?|B)?\s*", value or "", flags=re.IGNORECASE)
    if not match:
        return None
    unit = (match.group(2) or "").upper().rstrip("B")
    scale = {"K": 1024, "M": 1024 ** 2, "G": GIB, "T": 1024 ** 4}.get(unit, 1)
    return round(float(match.group(1)) * scale / GIB, 2)


def parse_wslconfig(text):
    """memory= under [wsl2] (GiB) and autoMemoryReclaim= under [experimental]."""
    section = ""
    memory = reclaim = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        header = re.match(r"^\[([^\]]+)\]", line)
        if header:
            section = header.group(1).strip().lower()
            continue
        key, separator, value = line.partition("=")
        if not separator or line.startswith(("#", ";")):
            continue
        key, value = key.strip().lower(), value.split("#", 1)[0].strip()
        if section == "wsl2" and key == "memory" and memory is None:
            memory = wsl_memory_value_gib(value)
        elif section == "experimental" and key == "automemoryreclaim" and reclaim is None:
            reclaim = value
    return {"memoryGiB": memory, "reclaim": reclaim}


def read_meminfo(path="/proc/meminfo"):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            name, _, rest = line.partition(":")
            if name in ("MemTotal", "MemFree") and rest.split():
                values[name] = int(rest.split()[0]) * 1024
    except OSError:
        return {}
    return {"memTotalGiB": gib(values["MemTotal"]) if "MemTotal" in values else None,
            "memFreeGiB": gib(values["MemFree"]) if "MemFree" in values else None}


def powershell_path():
    found = shutil.which("powershell.exe")
    if found:
        return found
    return WINDOWS_POWERSHELL if Path(WINDOWS_POWERSHELL).is_file() else None


def windows_report(ports):
    """Windows' memory status, .wslconfig and port probes, or why not."""
    executable = powershell_path()
    if not executable:
        return {"available": False, "reason": "Windows PowerShell is not reachable from WSL (interop off?)"}
    script = POWERSHELL_SCRIPT.replace("__PORTS__", ",".join(str(int(port)) for port in ports))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=POWERSHELL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": f"Windows PowerShell did not answer: {exc}"}
    if result.returncode != 0:
        return {"available": False, "reason": f"Windows PowerShell exited {result.returncode}"}
    try:
        data = json.loads(result.stdout.lstrip(chr(0xFEFF)))
    except ValueError:
        return {"available": False, "reason": "Windows PowerShell returned no memory status"}
    numbers = ("totalPhys", "availPhys", "totalPageFile", "availPageFile")
    if not isinstance(data, dict) or not all(isinstance(data.get(name), (int, float)) for name in numbers):
        return {"available": False, "reason": "Windows PowerShell returned no memory status"}
    raw_ports = data.get("ports") if isinstance(data.get("ports"), dict) else {}
    return {
        "available": True,
        "physicalGiB": gib(data["totalPhys"]),
        "commitLimitGiB": gib(data["totalPageFile"]),
        "commitAvailableGiB": gib(data["availPageFile"]),
        "wslconfig": parse_wslconfig(data.get("wslconfig") if isinstance(data.get("wslconfig"), str) else ""),
        "ports": {str(port): str(state) for port, state in raw_ports.items()},
    }


def docker_stat(container, target):
    try:
        out = subprocess.run(["docker", "exec", container, "stat", "-Lc", "%d:%i:%f", "--", target],
                             capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
        listing = subprocess.run(["docker", "exec", container, "ls", "-A", "--", target],
                                 capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r"([0-9]+):([0-9]+):([0-9a-f]+)", out.stdout.strip())
    if out.returncode != 0 or not match or listing.returncode != 0:
        return None
    return {"device": int(match.group(1)), "inode": int(match.group(2)),
            "entries": len([name for name in listing.stdout.splitlines() if name.strip()])}


def bind_mount_report(install_dir, container, docker_ok):
    """Does the dashboard API's /data show this installation's data directory?"""
    source = install_dir / "data"
    report = {"checked": False, "container": container, "target": "/data", "source": str(source)}
    if not docker_ok:
        return report
    try:
        host = source.stat()
        host_entries = len(os.listdir(source))
    except OSError:
        return report
    seen = docker_stat(container, "/data")
    if seen is None:
        return report
    same = (seen["device"], seen["inode"]) == (host.st_dev, host.st_ino)
    report.update(checked=True, state="match" if same else "stale",
                  containerEntries=seen["entries"], hostEntries=host_entries)
    return report


def current_boot_id(path="/proc/sys/kernel/random/boot_id"):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def self_heal_report(install_dir, boot_id):
    """The host agent's record for this WSL boot, None for another boot."""
    try:
        record = json.loads((install_dir / "data" / "wsl-self-heal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not boot_id or record.get("bootId") != boot_id:
        return None
    return {key: record.get(key) for key in ("state", "detail", "next", "repairs", "deadPorts", "portsNext",
                                             "updatedAt")}


def finding(diag_id, severity, title, evidence, impact, next_steps):
    return {"id": diag_id, "severity": severity, "confidence": "high", "title": title,
            "evidence": [{"source": source, "detail": detail} for source, detail in evidence],
            "impact": impact, "next_steps": next_steps}


def memory_findings(windows, wsl):
    found = []
    available = windows.get("commitAvailableGiB")
    limit = windows.get("commitLimitGiB")
    evidence = ("GlobalMemoryStatusEx", f"ullAvailPageFile {available} GiB of a {limit} GiB commit limit")
    if available is not None and available < COMMIT_WARN_GIB:
        critical = available < COMMIT_CRITICAL_GIB
        found.append(finding(
            "ODS-WINDOWS-COMMIT-EXHAUSTED" if critical else "ODS-WINDOWS-COMMIT-LOW",
            "blocker" if critical else "warn",
            f"Windows has {available} GiB of memory left to lend (commit headroom)",
            [evidence],
            "When it reaches zero Windows refuses memory to every program: installs and image turns fail and "
            "WSL itself can crash and restart, taking ODS's containers with it.",
            ["Close programs you do not need on Windows (other users' sessions count too).",
             "Rerun the ODS installer: on a PC whose model runs on Windows it limits WSL's memory.",
             "An administrator can give Windows more room with a larger page file: System Properties > "
             "Advanced > Performance > Settings > Advanced > Virtual memory. ODS never changes it.",
             HELP]))
    free = wsl.get("memFreeGiB")
    if available is not None and free is not None and available >= COMMIT_CRITICAL_GIB and free > available:
        found.append(finding(
            "ODS-WSL-CAN-EXHAUST-COMMIT", "warn",
            f"WSL may still take {free} GiB, more than the {available} GiB Windows has left",
            [evidence, ("/proc/meminfo", f"MemFree {free} GiB inside WSL")],
            "If WSL grows into that memory, Windows runs out and WSL can crash.",
            ["Set a WSL memory limit: rerun the ODS installer, or add memory= under [wsl2] in "
             "%USERPROFILE%\\.wslconfig, then restart WSL.", HELP]))
    configured = (windows.get("wslconfig") or {}).get("memoryGiB")
    total = wsl.get("memTotalGiB")
    if configured is not None and total is not None and total > configured + LIMIT_SLACK_GIB:
        found.append(finding(
            "ODS-WSL-MEMORY-LIMIT-PENDING", "warn",
            f"WSL still runs with {total} GiB; the {configured} GiB limit in .wslconfig applies after WSL restarts",
            [("%USERPROFILE%\\.wslconfig", f"memory= {configured} GiB"),
             ("/proc/meminfo", f"MemTotal {total} GiB")],
            "Until WSL restarts it can still use more memory than Windows can spare.",
            [WSL_RESTART]))
    return found


def mount_findings(bind):
    if bind.get("state") != "stale":
        return []
    entries = bind.get("containerEntries")
    view = "an empty folder" if entries == 0 else f"another folder ({entries} entries)"
    return [finding(
        "ODS-WSL-BIND-MOUNTS-STALE", "blocker",
        f"{bind['container']} sees {view} at /data instead of your ODS data",
        [(f"docker exec {bind['container']} stat /data", "a different folder than the host's"),
         (bind["source"], f"{bind.get('hostEntries')} entries on the host")],
        "After WSL restarted, Docker Desktop brought the containers back without their data: the dashboard "
        "shows the local model runtime as unavailable and Portal cannot work.",
        ["Run: ods repair mounts (it recreates the affected containers from images already here, after "
         "checking that a new container sees your data)", HELP])]


def port_findings(windows, services):
    dead = [(name, port, windows.get("ports", {}).get(str(port))) for name, port in services
            if windows.get("ports", {}).get(str(port)) in ("refused", "closed")]
    if not dead:
        return []
    names = ", ".join(f"{name} (localhost:{port} {state})" for name, port, state in dead)
    blocker = any(name == "dashboard" for name, _port, _state in dead)
    return [finding(
        "ODS-DOCKER-DESKTOP-PORTS-DEAD", "blocker" if blocker else "warn",
        f"Windows cannot reach {names} although the containers run",
        [("Windows 127.0.0.1", names)],
        "Docker Desktop stopped forwarding these ports to Windows, so the browser cannot open them.",
        [DOCKER_RESTART, HELP])]


def self_heal_findings(record):
    if not record:
        return []
    found = []
    if record.get("next") and record.get("state") not in ("healthy", "repaired", "repairing", "checking"):
        found.append(finding(
            "ODS-WSL-SELF-HEAL-NEEDS-HELP", "warn",
            f"After this WSL start the host agent could not finish its container repair ({record.get('state')})",
            [("data/wsl-self-heal.json", str(record.get("detail") or record.get("state")))],
            "Containers may still miss their data until the step below is done.",
            [str(record["next"]), HELP]))
    if record.get("deadPorts"):
        found.append(finding(
            "ODS-WSL-PORTS-STILL-DEAD", "warn",
            "Published ports stayed dead after one restart: " + ", ".join(map(str, record["deadPorts"])),
            [("data/wsl-self-heal.json", "deadPorts")],
            "Docker Desktop is not forwarding these containers' ports.",
            [str(record.get("portsNext") or DOCKER_RESTART), HELP]))
    return found


def parse_port(value):
    name, separator, port = value.rpartition(":")
    if not separator or not name or not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
        raise argparse.ArgumentTypeError(f"expected service:port, got {value!r}")
    return name, int(port)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--docker", default="false", help="true when the doctor reached the Docker daemon")
    parser.add_argument("--dashboard-api-container", default="ods-dashboard-api")
    parser.add_argument("--port", action="append", type=parse_port, default=[],
                        help="service:port of a running ODS HTTP service, probed from Windows")
    args = parser.parse_args(argv)
    install_dir = Path(args.install_dir).resolve()
    # The dashboard first: it is the page the owner opens.
    services = sorted(args.port, key=lambda service: service[0] != "dashboard")[:MAX_PORTS]
    windows = windows_report([port for _name, port in services])
    wsl = read_meminfo()
    bind = bind_mount_report(install_dir, args.dashboard_api_container, args.docker == "true")
    record = self_heal_report(install_dir, current_boot_id())
    findings = mount_findings(bind) + self_heal_findings(record)
    if windows.get("available"):
        findings = memory_findings(windows, wsl) + findings + port_findings(windows, services)
    print(json.dumps({"applies": True, "windows": windows, "wsl": wsl, "bindMounts": bind,
                      "selfHeal": record, "findings": findings}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
