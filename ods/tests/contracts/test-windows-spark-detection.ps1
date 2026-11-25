# RTX Spark detection for the Windows Portal (Windows on Arm with an NVIDIA
# CUDA GPU), plus the NPU filter in Get-GpuInfo. The machine architecture,
# WMI and nvidia-smi are fixtures: the real nvidia-smi is never run, and only
# temporary files are written. Device rows come from the owner's RTX Spark
# probe in tests/fixtures/rtx-spark.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-portal-setup.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$fixtures = Join-Path $root 'tests/fixtures/rtx-spark'
function Read-Fixture([string]$Name) {
    return @([IO.File]::ReadAllLines((Join-Path $fixtures $Name)) | Where-Object { $_.Trim() })
}
$sparkName = 'NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)'
$windowsRows = Read-Fixture 'nvidia-smi-windows.csv'
# The factory firmware split was not measured; its figure replaces the
# measured one in the real rows.
$factoryRows = @($windowsRows | ForEach-Object { $_.Replace(' 98240 MiB,', ' 63936 MiB,') })

# --- Fixtures -----------------------------------------------------------------
$probe = Read-Fixture 'nvidia-smi-windows-probe.txt'
$composed = @($windowsRows | ForEach-Object { ($_ -split ',')[0..4] -join ',' })
Check ((@($probe | Select-Object -Skip 1) -join "`n") -ceq ($composed -join "`n")) 'the setup-query fixture rows are the probe capture plus driver_version'
Check ($factoryRows[0] -match ' 63936 MiB,' -and $factoryRows[0] -ne $windowsRows[0]) 'the factory-split rows differ from the measured rows only in the memory size'
$private = @(
    @{ Name = 'a device UUID'; Pattern = '(?i)\b(GPU|DLA|MIG)-[0-9a-f]{8}-' },
    @{ Name = 'a Windows account id'; Pattern = 'S-1-5-21-[0-9]' },
    @{ Name = 'a user path'; Pattern = '(?i)[a-z]:\\Users\\|/mnt/[a-z]/Users/|/home/[a-z]' },
    @{ Name = 'a host name path'; Pattern = '\\\\[A-Za-z0-9-]+\\' },
    @{ Name = 'an ODS task name'; Pattern = '(?i)ODS-WSL-[0-9a-f]{6,}|ODSLlamaServerRuntime-S-' },
    @{ Name = 'a network hardware address'; Pattern = '(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b' },
    @{ Name = 'engine files'; Pattern = '(?i)\.odsw\b|ods-inference' })
foreach ($file in @(Get-ChildItem -LiteralPath $fixtures -File)) {
    $text = [IO.File]::ReadAllText($file.FullName)
    foreach ($rule in $private) {
        Check ($text -notmatch $rule.Pattern) "fixture $($file.Name) holds no $($rule.Name)"
    }
}

# --- Machine architecture -----------------------------------------------------
Check (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = 'ARM64'; ProcessArchitecture = 'AMD64'; ProcessorArchitectures = @(12) })) 'an x64 PowerShell emulated on Windows on Arm (machine ARM64, process AMD64) is Arm64'
Check (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = 'ARM64'; ProcessArchitecture = 'ARM64'; ProcessorArchitectures = @() })) 'the machine environment value alone is enough (WMI unavailable)'
Check (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = ''; ProcessArchitecture = 'AMD64'; ProcessorArchitectures = @(12) })) 'Win32_Processor.Architecture 12 alone is enough'
Check (-not (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = 'AMD64'; ProcessArchitecture = 'ARM64'; ProcessorArchitectures = @(9) }))) 'the process architecture never decides'
Check (-not (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = 'AMD64'; ProcessArchitecture = 'AMD64'; ProcessorArchitectures = @(9) }))) 'an x64 PC is not Arm64'
Check (-not (Test-ODSSparkArm64Facts ([pscustomobject]@{ MachineArchitecture = $null; ProcessArchitecture = $null; ProcessorArchitectures = @() }))) 'no facts (PowerShell on Linux) is not Arm64'

$processor = Get-Content -LiteralPath (Join-Path $fixtures 'windows-processor.json') -Raw | ConvertFrom-Json
$facts = & {
    function Get-ODSSparkMachineArchitecture { return 'AMD64' }
    function Get-CimInstance { param([string]$ClassName, $ErrorAction) if ($ClassName -ne 'Win32_Processor') { throw "unexpected WMI class $ClassName" } return $processor }
    Get-ODSSparkArchitectureFacts
}
Check ((@($facts.ProcessorArchitectures) -join ',') -eq '12' -and $facts.MachineArchitecture -eq 'AMD64') 'the facts reader takes the Spark processor architecture from Win32_Processor'
Check (Test-ODSSparkArm64Facts $facts) 'the Spark processor fixture reads as Arm64 even beside an AMD64 machine value'
$arm = & {
    function Get-ODSSparkMachineArchitecture { return 'ARM64' }
    function Get-ODSSparkProcessorArchitectures { return @() }
    Test-ODSSparkWindowsArm64
}
Check ($arm -eq $true) 'Test-ODSSparkWindowsArm64 combines the machine readers'
if ($onWindows) {
    # The exception type lives in the CIM assembly, which this loads.
    Import-Module CimCmdlets
    $noWmi = & {
        function Get-CimInstance { param([string]$ClassName, $ErrorAction) throw [Microsoft.Management.Infrastructure.CimException]::new('fixture: WMI is unavailable') }
        @(Get-ODSSparkProcessorArchitectures)
    }
    Check ($noWmi.Count -eq 0) 'a WMI failure leaves only the machine environment value to decide'
}

# --- nvidia-smi rows ----------------------------------------------------------
$rows = @(ConvertFrom-ODSNvidiaSmiCsv $windowsRows)
Check ($rows.Count -eq 2) 'both probe rows are parsed'
$n1x = $rows[0]
Check ($n1x.Name -ceq $sparkName -and $n1x.PciDeviceId -ceq '0x2e03' -and $n1x.ComputeCap -ceq '12.1' -and
    $n1x.MemoryMiB -eq 98240 -and $n1x.DriverModel -ceq 'WDDM' -and $n1x.DriverVersion -ceq '616.62' -and $n1x.IsCudaGpu) 'the N1X row is a CUDA GPU with its PCI id, compute capability, carve-out and driver'
$npu = $rows[1]
Check ($npu.Name -ceq 'NVIDIA NPU' -and -not $npu.IsCudaGpu -and $null -eq $npu.MemoryMiB -and $npu.PciDeviceId -eq '' -and $npu.ComputeCap -eq '') 'the NPU row ([N/A] memory, MCDM) is never a GPU'
$plain = @(ConvertFrom-ODSNvidiaSmiCsv @('NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU), 0x2E0310DE, 12.1, 98240, WDDM, 616.62'))
Check ($plain[0].MemoryMiB -eq 98240 -and $plain[0].IsCudaGpu) 'memory without units (--format nounits) is read the same'
$mcdm = @(ConvertFrom-ODSNvidiaSmiCsv @('NVIDIA Future Accelerator, 0x2F0010DE, 12.1, 16384, MCDM, 616.62'))
Check (-not $mcdm[0].IsCudaGpu) 'a compute-only MCDM device is never a GPU, even with a memory size'
$comma = @(ConvertFrom-ODSNvidiaSmiCsv @('NVIDIA Board, Rev 2, 0x2E0310DE, 12.1, 98240, WDDM, 616.62'))
Check ($comma[0].Name -ceq 'NVIDIA Board, Rev 2' -and $comma[0].MemoryMiB -eq 98240) 'a comma in a device name does not shift the other fields'
Check (@(ConvertFrom-ODSNvidiaSmiCsv @('', 'No devices were found', 'a, b, c')).Count -eq 0) 'short or empty lines are not devices'

# --- The nvidia-smi reader ------------------------------------------------------
$temp = Join-Path ([IO.Path]::GetTempPath()) ('ods-spark-detect-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $temp
try {
    $rowsFile = Join-Path $temp 'rows.csv'
    [IO.File]::WriteAllLines($rowsFile, [string[]]$windowsRows)
    $argsFile = Join-Path $temp 'args.txt'
    function New-FakeNvidiaSmi([int]$ExitCode) {
        if ($onWindows) {
            $path = Join-Path $temp "nvidia-smi-$ExitCode.cmd"
            [IO.File]::WriteAllText($path, "@echo off`r`n>`"$argsFile`" echo %*`r`ntype `"$rowsFile`"`r`nexit /b $ExitCode`r`n")
        } else {
            $path = Join-Path $temp "nvidia-smi-$ExitCode"
            [IO.File]::WriteAllText($path, "#!/bin/sh`nprintf '%s ' `"`$@`" > '$argsFile'`ncat '$rowsFile'`nexit $ExitCode`n")
            chmod +x $path
        }
        return $path
    }
    function Invoke-RowsReader($Executable) {
        $script:fakeSmi = $Executable
        return & {
            function Get-Command { param([string]$Name, $CommandType, $ErrorAction) if ($Name -ne 'nvidia-smi.exe') { throw "unexpected lookup $Name" } if ($script:fakeSmi) { return [pscustomobject]@{ Source = $script:fakeSmi } } }
            @(Get-ODSSparkNvidiaRows)
        }
    }
    $read = @(Invoke-RowsReader (New-FakeNvidiaSmi 0))
    $passed = ([IO.File]::ReadAllText($argsFile)).Trim()
    Check ($read.Count -eq 2 -and $read[0].IsCudaGpu -and -not $read[1].IsCudaGpu) 'the reader returns the driver rows'
    Check ($passed -ceq '--query-gpu=name,pci.device_id,compute_cap,memory.total,driver_model.current,driver_version --format=csv,noheader,nounits') 'the reader asks nvidia-smi for name, PCI id, compute capability, memory, driver model and version'
    Check (@(Invoke-RowsReader (New-FakeNvidiaSmi 9)).Count -eq 0) 'a failing nvidia-smi gives no rows'
    Check (@(Invoke-RowsReader $null).Count -eq 0) 'no nvidia-smi gives no rows'
    if ($onWindows) {
        $broken = Join-Path $temp 'nvidia-smi.exe'
        [IO.File]::WriteAllText($broken, 'not a program')
        $global:LASTEXITCODE = 0
        Check (@(Invoke-RowsReader $broken).Count -eq 0) 'an nvidia-smi that cannot start gives no rows and never throws'
    }
    $global:LASTEXITCODE = 0
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force
}

# --- Get-ODSSparkHost -----------------------------------------------------------
$script:arm64 = $false
$script:smiRows = @()
$script:smiAsked = 0
function Test-ODSSparkWindowsArm64 { return $script:arm64 }
function Get-ODSSparkNvidiaRows { $script:smiAsked++; return @(ConvertFrom-ODSNvidiaSmiCsv $script:smiRows) }

$script:smiRows = $windowsRows
Check ($null -eq (Get-ODSSparkHost) -and $script:smiAsked -eq 0) 'an x64 PC is never the RTX Spark, and nvidia-smi is not asked'
$script:smiRows = @('NVIDIA GeForce RTX 5070 Laptop GPU, 0x2F1810DE, 12.0, 8151, WDDM, 581.57')
Check ($null -eq (Get-ODSSparkHost)) 'an x64 PC with a GeForce is not the RTX Spark'
$script:arm64 = $true
$script:smiRows = @()
Check ($null -eq (Get-ODSSparkHost)) 'Windows on Arm without an NVIDIA driver is not the RTX Spark'
$script:smiRows = @($windowsRows[1])
Check ($null -eq (Get-ODSSparkHost)) 'Windows on Arm with only the NPU listed is not the RTX Spark'
$script:smiRows = @($windowsRows[0], $windowsRows[0])
Check ($null -eq (Get-ODSSparkHost)) 'Windows on Arm with two CUDA GPUs is not this route'
$script:smiRows = $windowsRows
$sparkHost = Get-ODSSparkHost
Check ($sparkHost.GpuName -ceq $sparkName -and $sparkHost.PciDeviceId -ceq '0x2e03' -and $sparkHost.ComputeCap -ceq '12.1' -and
    $sparkHost.CarveOutMiB -eq 98240 -and $sparkHost.DriverVersion -ceq '616.62' -and $sparkHost.DriverMajor -eq 616 -and
    $sparkHost.DriverModel -ceq 'WDDM') 'the RTX Spark is recognized with its GPU, carve-out and driver'
Check ((@($sparkHost.IgnoredDevices) -join '|') -ceq 'NVIDIA NPU') 'the NPU is listed as ignored, never as the GPU'
$script:smiRows = @($windowsRows[1], $windowsRows[0])
Check ((Get-ODSSparkHost).GpuName -ceq $sparkName) 'the NPU listed first does not hide the N1X'
$script:smiRows = $factoryRows
Check ((Get-ODSSparkHost).CarveOutMiB -eq 63936) 'the factory split is recognized with its smaller carve-out'
$script:smiRows = @('NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU), 0x2E0310DE, 12.1, 98240, WDDM, [N/A]')
Check ((Get-ODSSparkHost).DriverMajor -eq 0) 'an unreadable driver version is major 0, never a throw'

# Detection is engine-free: no parameters, and the functions on its path only
# ask the architecture readers and nvidia-smi. No file, setting or engine is read.
Check (@((Get-Command Get-ODSSparkHost).Parameters.Keys).Count -eq 0) 'Get-ODSSparkHost takes no source root or configuration'
$sparkAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/lib/wsl-portal-spark.ps1'), [ref]$null, [ref]$null)
$detection = @('Get-ODSSparkHost', 'Test-ODSSparkWindowsArm64', 'Get-ODSSparkArchitectureFacts', 'Get-ODSSparkMachineArchitecture',
    'Get-ODSSparkProcessorArchitectures', 'Test-ODSSparkArm64Facts', 'Get-ODSSparkNvidiaRows', 'ConvertFrom-ODSNvidiaSmiCsv')
$allowedCommands = $detection + @('Get-Command', 'Get-CimInstance', 'Where-Object', 'ForEach-Object', 'Select-Object')
$allowedMembers = @('GetEnvironmentVariable', 'Split', 'Trim', 'Substring', 'ToLowerInvariant')
foreach ($name in $detection) {
    $definition = $sparkAst.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name }, $true)
    Check ($null -ne $definition) "$name is defined in wsl-portal-spark.ps1"
    $commands = @($definition.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.InvocationOperator -ne 'Ampersand' }, $true) | ForEach-Object { $_.GetCommandName() })
    $members = @($definition.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true) | ForEach-Object { [string]$_.Member.Value })
    $unexpected = @($commands | Where-Object { $_ -notin $allowedCommands }) + @($members | Where-Object { $_ -notin $allowedMembers })
    $also = if ($unexpected.Count) { ' (also: ' + ($unexpected -join ', ') + ')' } else { '' }
    Check ($unexpected.Count -eq 0) "$name only reads the architecture and nvidia-smi$also"
}
$invoked = @($sparkAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.InvocationOperator -eq 'Ampersand' }, $true))
Check ($invoked.Count -eq 1 -and $invoked[0].Extent.Text -match '^& \$smi\.Source ') 'the only program detection starts is nvidia-smi'

# --- Get-GpuInfo: the NPU is not a second GPU ---------------------------------
# A function shadows the real nvidia-smi for Get-GpuInfo's bare-name call.
$script:gpuInfoRows = @()
function nvidia-smi { $global:LASTEXITCODE = 0; return $script:gpuInfoRows }
if ((Get-Command nvidia-smi).CommandType -ne 'Function') { throw 'the nvidia-smi fixture does not shadow the real program' }
$script:gpuInfoRows = @("$sparkName, 98240 MiB, 616.62, 12.1", 'NVIDIA NPU, [N/A], 616.62, [N/A]')
$gpu = Get-GpuInfo
Check ($gpu.Backend -eq 'nvidia' -and $gpu.Name -ceq $sparkName -and $gpu['Count'] -eq 1 -and $gpu.VramMB -eq 98240 -and $gpu.ComputeCap -eq '12.1') 'Get-GpuInfo counts the N1X once and not the NPU'
$script:gpuInfoRows = @('NVIDIA NPU, [N/A], 616.62, [N/A]', "$sparkName, 98240 MiB, 616.62, 12.1")
$gpu = Get-GpuInfo
Check ($gpu.Name -ceq $sparkName -and $gpu['Count'] -eq 1 -and $gpu.VramMB -eq 98240) 'Get-GpuInfo reads the N1X even when the NPU is listed first'
$script:gpuInfoRows = @('NVIDIA GeForce RTX 4090, 24564 MiB, 581.57, 8.9', 'NVIDIA GeForce RTX 4090, 24564 MiB, 581.57, 8.9')
$gpu = Get-GpuInfo
Check ($gpu['Count'] -eq 2 -and $gpu.VramMB -eq 24564 -and $gpu.Name -ceq 'NVIDIA GeForce RTX 4090') 'two x64 GPUs with memory sizes are unchanged'
$script:gpuInfoRows = @('NVIDIA GB10, [N/A], 580.95.05, 12.1')
$gpu = Get-GpuInfo
Check ($gpu.Backend -eq 'nvidia' -and $gpu['Count'] -eq 1 -and $gpu.VramMB -eq 0 -and $gpu.Name -ceq 'NVIDIA GB10') 'a lone [N/A] row is kept as today'
Remove-Item Function:\nvidia-smi
$global:LASTEXITCODE = 0

Write-Host "Passed $script:checks Windows RTX Spark detection contracts."
