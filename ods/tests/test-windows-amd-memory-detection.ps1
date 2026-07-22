$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$detectionPath = Join-Path $root "installers\windows\lib\detection.ps1"
. $detectionPath

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

Assert-Equal (Test-WindowsAmdDiscreteGpuName "AMD Radeon RX 9070 XT") $true "RX discrete classification"
Assert-Equal (Test-WindowsAmdDiscreteGpuName "AMD Radeon PRO W7900") $true "Radeon PRO classification"
Assert-Equal (Test-WindowsAmdDiscreteGpuName "AMD Radeon R9 390") $true "Legacy Radeon discrete classification"
Assert-Equal (Test-WindowsAmdDiscreteGpuName "AMD Radeon Vega 64") $true "Vega discrete classification"
Assert-Equal (Test-WindowsAmdDiscreteGpuName "AMD Radeon 8060S Graphics") $false "Strix integrated classification"
Assert-Equal (Test-WindowsAmdIntegratedGpuName "AMD Radeon(TM) Graphics") $true "Generic integrated classification"
Assert-Equal (Test-WindowsAmdIntegratedGpuName "AMD Radeon Vega 8 Graphics") $true "Vega integrated classification"
Assert-Equal (Test-WindowsAmdIntegratedGpuName "AMD Radeon(TM) 780M Graphics") $true "TM mobile integrated classification"
Assert-Equal (Test-WindowsAmdIntegratedGpuName "AMD Radeon(TM) RX Vega 11 Graphics") $true "TM RX Vega integrated classification"

$hybridAdapters = @(
    [pscustomobject]@{ Name = "AMD Radeon(TM) Graphics" },
    [pscustomobject]@{ Name = "AMD Radeon RX 9070 XT" }
)
Assert-Equal (Select-WindowsAmdPrimaryGpu -Gpus $hybridAdapters).Name `
    "AMD Radeon RX 9070 XT" "Hybrid adapter selection"
Assert-Equal (Get-WindowsAmdComputeGpuCount -Gpus $hybridAdapters) 1 `
    "Hybrid iGPU must not trigger multi-GPU"

$legacyHybridAdapters = @(
    [pscustomobject]@{ Name = "AMD Radeon Vega 8 Graphics" },
    [pscustomobject]@{ Name = "AMD Radeon R9 390" }
)
Assert-Equal (Select-WindowsAmdPrimaryGpu -Gpus $legacyHybridAdapters).Name `
    "AMD Radeon R9 390" "Legacy hybrid adapter selection"
Assert-Equal (Get-WindowsAmdComputeGpuCount -Gpus $legacyHybridAdapters) 1 `
    "Legacy hybrid iGPU must not trigger multi-GPU"

$dualDiscreteWithIgpu = @(
    [pscustomobject]@{ Name = "AMD Radeon(TM) Graphics" },
    [pscustomobject]@{ Name = "AMD Radeon RX 9070 XT" },
    [pscustomobject]@{ Name = "AMD Radeon PRO W7900" }
)
Assert-Equal (Get-WindowsAmdComputeGpuCount -Gpus $dualDiscreteWithIgpu) 2 `
    "Dual discrete AMD count"

Assert-Equal (ConvertTo-WindowsAmdAdapterRamBytes -Value ([int]-1048576)) `
    ([uint64]4293918720) "Signed WMI AdapterRAM reinterpretation"
Assert-Equal (ConvertTo-WindowsAmdAdapterRamBytes -Value "invalid") `
    ([uint64]0) "Invalid WMI AdapterRAM fallback"

Assert-Equal (Test-WindowsAmdUnifiedMemory `
    -GpuName "AMD Radeon RX 9070 XT" -ProcessorNames "AMD Ryzen 9 9950X" `
    -AdapterRamMB 4095 -SystemRamGB 64) $false "RX 9070 XT must not use system RAM"

Assert-Equal (Test-WindowsAmdUnifiedMemory `
    -GpuName "AMD Radeon 8060S Graphics" -ProcessorNames "AMD Ryzen AI MAX+ 395" `
    -AdapterRamMB 2048 -SystemRamGB 128) $true "Strix Halo unified classification"

Assert-Equal (Test-WindowsAmdUnifiedMemory `
    -GpuName "AMD Radeon 780M Graphics" -ProcessorNames "AMD Ryzen 7 7840U" `
    -AdapterRamMB 2048 -SystemRamGB 32) $true "Integrated Radeon classification"

$expectedName = "AMD Radeon RX 9070 XT"
$registryValue = [uint64]17095983104
function Get-ChildItem {
    param($LiteralPath, $ErrorAction)
    return @([pscustomobject]@{ PSPath = "mock:\\video\\0001" })
}
function Get-ItemProperty {
    param($LiteralPath, $ErrorAction)
    return [pscustomobject]@{
        DriverDesc = $expectedName
        'HardwareInformation.qwMemorySize' = $registryValue
    }
}

$registryVram = Get-WindowsAmdDedicatedVramMB `
    -GpuName $expectedName `
    -AdapterRamBytes ([uint64]4293918720)
Assert-Equal $registryVram 16304 "64-bit registry VRAM"

$registryValue = [BitConverter]::GetBytes([uint64]17095983104)
$registryByteVram = Get-WindowsAmdDedicatedVramMB `
    -GpuName $expectedName `
    -AdapterRamBytes ([uint64]4293918720)
Assert-Equal $registryByteVram 16304 "Byte-array registry VRAM"

# Regression: a genuine APU whose BIOS raises the dedicated-VRAM carve above
# 4 GB reports the true carve via the registry, but the unified-memory gate
# must still see the raw WMI-capped value — otherwise a real Strix Halo flips
# to "discrete" and mis-tiers.
$expectedName = "AMD Radeon(TM) 8060S Graphics"
$registryValue = [uint64](96GB)
$raisedCarveRecovered = Get-WindowsAmdDedicatedVramMB `
    -GpuName $expectedName `
    -AdapterRamBytes ([uint64]536870912)
Assert-Equal $raisedCarveRecovered 98304 "Raised-carve registry recovery still reads the true carve"
$raisedCarveUnified = Test-WindowsAmdUnifiedMemory `
    -GpuName $expectedName `
    -ProcessorNames "AMD Ryzen AI MAX+ PRO 395" `
    -AdapterRamMB 512 `
    -SystemRamGB 128
Assert-Equal $raisedCarveUnified $true "Raised-carve Strix Halo stays unified when gated on raw WMI value"

# Wiring: Get-GpuInfo must gate unified detection on the WMI-capped value and
# apply registry recovery only on the discrete branch.
$detectionSource = Get-Content -LiteralPath $detectionPath -Raw
if ($detectionSource -notmatch '(?s)\$adapterRamMB\s*=\s*\[math\]::Floor\(\$adapterRamBytes\s*/\s*1048576\)') {
    throw "Get-GpuInfo must feed the unified gate the raw WMI-capped adapter RAM"
}
if ($detectionSource -notmatch '(?s)\}\s*else\s*\{[^}]*Get-WindowsAmdDedicatedVramMB') {
    throw "Get-GpuInfo must apply registry VRAM recovery only on the discrete branch"
}

Write-Host "[PASS] Windows AMD discrete and unified memory detection"
