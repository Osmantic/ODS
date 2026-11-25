# NVIDIA RTX Spark (Windows on Arm with an NVIDIA CUDA GPU) for the Windows ->
# WSL Portal path: recognizing the machine.
#
# This file only detects the hardware. It reads no ODS configuration, needs no
# model engine and never changes anything. A machine counts as an RTX Spark
# when Windows runs on Arm and nvidia-smi lists exactly one CUDA GPU. The
# trigger is the platform, not a list of PCI ids, so a later N1-class device
# is judged by what its build can run, not refused by name. Windows also lists
# the NVIDIA NPU (driver model MCDM, no memory size); it is never a GPU here.

$script:ODSSparkNvidiaSmiQuery = 'name,pci.device_id,compute_cap,memory.total,driver_model.current,driver_version'
# Win32_Processor.Architecture: 12 is ARM64 (9 is x64).
$script:ODSSparkWmiArm64 = 12

function Get-ODSSparkMachineArchitecture {
    # The machine's architecture from the system environment, which Windows
    # keeps in the registry: an x64 PowerShell emulated on Windows on Arm sees
    # AMD64 in its own environment but ARM64 here.
    return [string][Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE', 'Machine')
}

function Get-ODSSparkProcessorArchitectures {
    # Win32_Processor.Architecture of each processor. WMI answers from the
    # native provider, so emulation does not change it. Empty where WMI is
    # unavailable (PowerShell on Linux, or a broken WMI service).
    if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) { return @() }
    try {
        return @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop | ForEach-Object { [int]$_.Architecture })
    } catch [Microsoft.Management.Infrastructure.CimException] {
        # The machine environment value still decides.
        return @()
    }
}

function Get-ODSSparkArchitectureFacts {
    return [pscustomobject]@{
        MachineArchitecture    = Get-ODSSparkMachineArchitecture
        ProcessArchitecture    = [string]$env:PROCESSOR_ARCHITECTURE
        ProcessorArchitectures = @(Get-ODSSparkProcessorArchitectures)
    }
}

function Test-ODSSparkArm64Facts($Facts) {
    # Either machine-level signal is enough. The process architecture is
    # never used: it is AMD64 in an emulated x64 PowerShell.
    if ([string]$Facts.MachineArchitecture -ieq 'ARM64') { return $true }
    return [bool](@($Facts.ProcessorArchitectures | Where-Object { [int]$_ -eq $script:ODSSparkWmiArm64 }).Count -gt 0)
}

function Test-ODSSparkWindowsArm64 {
    return (Test-ODSSparkArm64Facts (Get-ODSSparkArchitectureFacts))
}

function ConvertFrom-ODSNvidiaSmiCsv([string[]]$Lines) {
    <#
    .SYNOPSIS
        Rows of nvidia-smi --query-gpu=<ODSSparkNvidiaSmiQuery>
        --format=csv,noheader[,nounits]: one object per device.
    #>
    $rows = @()
    foreach ($line in @($Lines)) {
        $fields = @(([string]$line).Split(','))
        # The five fields after the name never contain a comma; a name could.
        if ($fields.Count -lt 6) { continue }
        $tail = @($fields[($fields.Count - 5)..($fields.Count - 1)] | ForEach-Object { $_.Trim() })
        $name = (($fields[0..($fields.Count - 6)]) -join ',').Trim()
        if (-not $name) { continue }
        $pci = $tail[0]
        $pciDeviceId = if ($pci -match '^0x[0-9A-Fa-f]{4}') { $pci.Substring(0, 6).ToLowerInvariant() } else { '' }
        $computeCap = if ($tail[1] -match '^[0-9]+\.[0-9]+$') { $tail[1] } else { '' }
        $memoryMiB = $null
        if ($tail[2] -match '^([0-9]+)(\s*MiB)?$') { $memoryMiB = [long]$Matches[1] }
        $driverModel = $tail[3]
        $rows += [pscustomobject]@{
            Name          = $name
            PciDeviceId   = $pciDeviceId
            ComputeCap    = $computeCap
            MemoryMiB     = $memoryMiB
            DriverModel   = $driverModel
            DriverVersion = $tail[4]
            # The NPU has no memory size, no compute capability and the
            # compute-only MCDM driver model.
            IsCudaGpu     = ($null -ne $memoryMiB -and [bool]$computeCap -and $driverModel -ine 'MCDM')
        }
    }
    return $rows
}

function Get-ODSSparkNvidiaRows {
    # Every device the Windows NVIDIA driver lists, or none when there is no
    # driver or nvidia-smi fails. Never throws.
    $smi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $smi) { return @() }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $smi.Source "--query-gpu=$script:ODSSparkNvidiaSmiQuery" '--format=csv,noheader,nounits' 2>$null)
        $code = $LASTEXITCODE
    } catch [System.Management.Automation.ApplicationFailedException] {
        # nvidia-smi could not be started (for example blocked by policy).
        return @()
    } finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { return @() }
    return @(ConvertFrom-ODSNvidiaSmiCsv ([string[]]$lines))
}

function Get-ODSSparkHost {
    <#
    .SYNOPSIS
        $null on every machine but an RTX Spark. There it returns the GPU
        facts the Spark route needs. Never throws and reads no configuration.
    #>
    # Every other Windows PC stops at the architecture check, before the
    # NVIDIA driver is asked anything.
    if (-not (Test-ODSSparkWindowsArm64)) { return $null }
    $rows = @(Get-ODSSparkNvidiaRows)
    $gpus = @($rows | Where-Object { $_.IsCudaGpu })
    if ($gpus.Count -ne 1) { return $null }
    $gpu = $gpus[0]
    $driverMajor = if ($gpu.DriverVersion -match '^([0-9]+)\.') { [int]$Matches[1] } else { 0 }
    return [pscustomobject]@{
        GpuName        = $gpu.Name
        PciDeviceId    = $gpu.PciDeviceId
        ComputeCap     = $gpu.ComputeCap
        # The firmware's GPU memory split, as nvidia-smi reports it.
        CarveOutMiB    = [long]$gpu.MemoryMiB
        DriverVersion  = $gpu.DriverVersion
        DriverMajor    = $driverMajor
        DriverModel    = $gpu.DriverModel
        IgnoredDevices = @($rows | Where-Object { -not $_.IsCudaGpu } | ForEach-Object { $_.Name })
    }
}
