# ============================================================================
# ODS Windows Native Desktop -- DirectX UserGpuPreferences & GPU Selection
# ============================================================================
# Part of: installers/windows/lib/
# Purpose:
#   1. Read and write Windows OS per-application GPU preferences in
#      HKCU:\Software\Microsoft\DirectX\UserGpuPreferences
#      (Settings > System > Display > Graphics).
#   2. Enforce ODS's GPU-First policy by default (GpuPreference=2, High
#      Performance discrete GPU) for ODS.exe and llama-server.exe when no
#      prior user preference exists, while preserving any existing DirectX
#      flags (such as AppStatus=0;) and honoring user overrides when switched
#      to Power Saving (GpuPreference=1) in Windows Settings.
#   3. Resolve the active GPU adapter for Windows Native Desktop across
#      NVIDIA, AMD, Intel Arc, integrated GPUs, and CPU fallback.
# ============================================================================

$script:ODSDirectXGpuPreferencesRegistryPath = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'

function ConvertFrom-ODSDirectXGpuPreferenceString {
    <#
    .SYNOPSIS
        Parse a Windows DirectX UserGpuPreferences value string such as
        "GpuPreference=2;" or "AppStatus=0;GpuPreference=1;".
    .OUTPUTS
        PSCustomObject with PreferenceCode (0, 1, 2, or $null), Mode
        ('let_windows_decide', 'power_saving', 'high_performance', or 'unset'),
        PreferDiscreteGpu ([bool]), and Tokens ([ordered] hashtable).
    #>
    param([AllowNull()][string]$RawValue)

    $tokens = [ordered]@{}
    $code = $null
    if (-not [string]::IsNullOrWhiteSpace($RawValue)) {
        foreach ($part in ($RawValue -split ';')) {
            $trimmed = $part.Trim()
            if (-not $trimmed -or -not $trimmed.Contains('=')) { continue }
            $kv = $trimmed -split '=', 2
            $k = $kv[0].Trim()
            $v = $kv[1].Trim()
            if (-not $k) { continue }
            $tokens[$k] = $v
            if ($k -ieq 'GpuPreference' -and $v -match '^[0-9]+$') {
                $code = [int]$v
            }
        }
    }

    switch ($code) {
        1 {
            return [pscustomobject]@{
                PreferenceCode    = 1
                Mode              = 'power_saving'
                PreferDiscreteGpu = $false
                RawValue          = [string]$RawValue
                Tokens            = $tokens
            }
        }
        2 {
            return [pscustomobject]@{
                PreferenceCode    = 2
                Mode              = 'high_performance'
                PreferDiscreteGpu = $true
                RawValue          = [string]$RawValue
                Tokens            = $tokens
            }
        }
        0 {
            return [pscustomobject]@{
                PreferenceCode    = 0
                Mode              = 'let_windows_decide'
                PreferDiscreteGpu = $true
                RawValue          = [string]$RawValue
                Tokens            = $tokens
            }
        }
        default {
            return [pscustomobject]@{
                PreferenceCode    = $null
                Mode              = 'unset'
                PreferDiscreteGpu = $true
                RawValue          = [string]$RawValue
                Tokens            = $tokens
            }
        }
    }
}

function ConvertTo-ODSDirectXGpuPreferenceString {
    <#
    .SYNOPSIS
        Build a semicolon-terminated DirectX UserGpuPreferences value string,
        preserving any existing non-GpuPreference tokens (such as AppStatus=0).
    #>
    param(
        [Parameter(Mandatory = $true)][ValidateSet(0, 1, 2)][int]$PreferenceCode,
        [AllowNull()][string]$ExistingRawValue = ''
    )

    $parsed = ConvertFrom-ODSDirectXGpuPreferenceString -RawValue $ExistingRawValue
    $out = [ordered]@{}
    foreach ($key in $parsed.Tokens.Keys) {
        if ($key -ine 'GpuPreference') {
            $out[$key] = $parsed.Tokens[$key]
        }
    }
    $out['GpuPreference'] = [string]$PreferenceCode
    $segments = foreach ($key in $out.Keys) { "$key=$($out[$key])" }
    return (($segments -join ';') + ';')
}

function Get-ODSDirectXGpuPreferenceEntries {
    <#
    .SYNOPSIS
        Read all value entries from HKCU:\Software\Microsoft\DirectX\UserGpuPreferences
        (or a custom RegistryPath). Returns an ordered hashtable of path -> string.
    #>
    param([string]$RegistryPath = $script:ODSDirectXGpuPreferencesRegistryPath)

    $entries = [ordered]@{}
    if (-not (Test-Path -LiteralPath $RegistryPath)) { return $entries }
    try {
        $item = Get-Item -LiteralPath $RegistryPath -ErrorAction Stop
        foreach ($valueName in $item.GetValueNames()) {
            if ([string]::IsNullOrWhiteSpace($valueName)) { continue }
            $val = $item.GetValue($valueName)
            if ($val -is [string]) {
                $entries[$valueName] = $val
            }
        }
    } catch { }
    return $entries
}

function Get-ODSWindowsUserGpuPreference {
    <#
    .SYNOPSIS
        Resolve the effective Windows OS GPU preference for ODS executables.
        Defaults to GPU-First (high_performance, GpuPreference=2,
        PreferDiscreteGpu=$true) when no explicit Windows Graphics setting
        exists. Honors Power Saving (GpuPreference=1) when set in Windows.
    #>
    param(
        [string[]]$ExecutablePaths = @(),
        [AllowNull()][System.Collections.IDictionary]$Entries = $null,
        [string]$RegistryPath = $script:ODSDirectXGpuPreferencesRegistryPath
    )

    if ($null -eq $Entries) {
        $Entries = Get-ODSDirectXGpuPreferenceEntries -RegistryPath $RegistryPath
    }

    $normalizedEntries = [ordered]@{}
    foreach ($entryName in $Entries.Keys) {
        $normEntry = ([string]$entryName).Trim().Replace('/', '\').ToLowerInvariant()
        $normalizedEntries[$normEntry] = [pscustomobject]@{
            OriginalKey = [string]$entryName
            RawValue    = [string]$Entries[$entryName]
        }
    }

    # 1. Exact path match in caller-specified ExecutablePaths priority order.
    #    If any matched executable is explicitly set to power_saving (1), honor it first.
    $matchedPrefs = [Collections.Generic.List[object]]::new()
    foreach ($p in $ExecutablePaths) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        $normTarget = ([string]$p).Trim().Replace('/', '\').ToLowerInvariant()
        if ($normalizedEntries.Contains($normTarget)) {
            $entryObj = $normalizedEntries[$normTarget]
            $parsed = ConvertFrom-ODSDirectXGpuPreferenceString -RawValue $entryObj.RawValue
            if ($null -ne $parsed.PreferenceCode) {
                $matchedPrefs.Add([pscustomobject]@{
                    Mode              = $parsed.Mode
                    PreferenceCode    = $parsed.PreferenceCode
                    PreferDiscreteGpu = $parsed.PreferDiscreteGpu
                    Source            = 'windows_directx_user_gpu_preferences'
                    MatchedExecutable = $entryObj.OriginalKey
                    RawValue          = $parsed.RawValue
                })
            }
        }
    }
    if ($matchedPrefs.Count -gt 0) {
        $powerSaveMatch = @($matchedPrefs | Where-Object { $_.PreferenceCode -eq 1 } | Select-Object -First 1)
        if ($powerSaveMatch.Count -eq 1) { return $powerSaveMatch[0] }
        return $matchedPrefs[0]
    }

    # 2. Leaf executable match for ODS.exe or llama-server.exe under an ODS directory
    $targetLeaves = @('ods.exe', 'llama-server.exe', 'ods-setup.exe')
    $leafMatches = [Collections.Generic.List[object]]::new()
    foreach ($normEntry in $normalizedEntries.Keys) {
        $leaf = Split-Path -Leaf $normEntry
        if ($leaf -in $targetLeaves -and ($normEntry.Contains('\ods\') -or $ExecutablePaths.Count -eq 0)) {
            $entryObj = $normalizedEntries[$normEntry]
            $parsed = ConvertFrom-ODSDirectXGpuPreferenceString -RawValue $entryObj.RawValue
            if ($null -ne $parsed.PreferenceCode) {
                $leafMatches.Add([pscustomobject]@{
                    Mode              = $parsed.Mode
                    PreferenceCode    = $parsed.PreferenceCode
                    PreferDiscreteGpu = $parsed.PreferDiscreteGpu
                    Source            = 'windows_directx_user_gpu_preferences'
                    MatchedExecutable = $entryObj.OriginalKey
                    RawValue          = $parsed.RawValue
                })
            }
        }
    }
    if ($leafMatches.Count -gt 0) {
        $powerSaveLeaf = @($leafMatches | Where-Object { $_.PreferenceCode -eq 1 } | Select-Object -First 1)
        if ($powerSaveLeaf.Count -eq 1) { return $powerSaveLeaf[0] }
        return $leafMatches[0]
    }

    # 3. Default GPU-First policy
    return [pscustomobject]@{
        Mode              = 'high_performance'
        PreferenceCode    = 2
        PreferDiscreteGpu = $true
        Source            = 'default_gpu_first'
        MatchedExecutable = $null
        RawValue          = 'GpuPreference=2;'
    }
}

function Set-ODSWindowsUserGpuPreference {
    <#
    .SYNOPSIS
        Register ODS executables (ODS.exe, llama-server.exe) in Windows
        DirectX UserGpuPreferences.
    .DESCRIPTION
        When -RespectExisting is set (the installer default), an executable
        that already has an explicit GpuPreference in the registry is kept as
        the user configured it in Windows Settings. When -RespectExisting is
        $false (for example, when the user toggles GPU mode inside ODS), the
        requested mode is written while preserving other DirectX tokens.
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$ExecutablePaths,
        [ValidateSet('high_performance', 'power_saving', 'let_windows_decide')]
        [string]$Mode = 'high_performance',
        [bool]$RespectExisting = $true,
        [string]$RegistryPath = $script:ODSDirectXGpuPreferencesRegistryPath
    )

    $code = switch ($Mode) {
        'power_saving'       { 1 }
        'let_windows_decide' { 0 }
        default              { 2 }
    }

    if (-not (Test-Path -LiteralPath $RegistryPath)) {
        New-Item -Path $RegistryPath -Force | Out-Null
    }

    $existingEntries = Get-ODSDirectXGpuPreferenceEntries -RegistryPath $RegistryPath
    $results = [Collections.Generic.List[object]]::new()

    foreach ($rawPath in $ExecutablePaths) {
        if ([string]::IsNullOrWhiteSpace($rawPath)) { continue }
        $fullPath = [IO.Path]::GetFullPath($rawPath)
        $existingKey = $null
        foreach ($k in $existingEntries.Keys) {
            if ([string]::Equals($k, $fullPath, [StringComparison]::OrdinalIgnoreCase)) {
                $existingKey = $k
                break
            }
        }

        $existingRaw = if ($existingKey) { [string]$existingEntries[$existingKey] } else { '' }
        $parsedExisting = ConvertFrom-ODSDirectXGpuPreferenceString -RawValue $existingRaw

        if ($RespectExisting -and $null -ne $parsedExisting.PreferenceCode) {
            $results.Add([pscustomobject]@{
                ExecutablePath    = $fullPath
                Written           = $false
                KeptExisting      = $true
                PreferenceCode    = $parsedExisting.PreferenceCode
                Mode              = $parsedExisting.Mode
                PreferDiscreteGpu = $parsedExisting.PreferDiscreteGpu
                RawValue          = $existingRaw
            })
            continue
        }

        $targetKey = if ($existingKey) { $existingKey } else { $fullPath }
        $newRaw = ConvertTo-ODSDirectXGpuPreferenceString -PreferenceCode $code -ExistingRawValue $existingRaw
        New-ItemProperty -LiteralPath $RegistryPath -Name $targetKey -Value $newRaw -PropertyType String -Force | Out-Null
        $parsedNew = ConvertFrom-ODSDirectXGpuPreferenceString -RawValue $newRaw
        $results.Add([pscustomobject]@{
            ExecutablePath    = $fullPath
            Written           = $true
            KeptExisting      = $false
            PreferenceCode    = $parsedNew.PreferenceCode
            Mode              = $parsedNew.Mode
            PreferDiscreteGpu = $parsedNew.PreferDiscreteGpu
            RawValue          = $newRaw
        })
    }

    return @($results)
}

function Select-ODSWindowsDesktopGpu {
    <#
    .SYNOPSIS
        Select the active GPU adapter for Windows Native Desktop given a
        detected discrete GPU (Get-GpuInfo), any integrated GPU adapters, and
        the user's DirectX UserGpuPreference.
    #>
    param(
        [Parameter(Mandatory = $true)]$PrimaryGpuInfo,
        [object[]]$AllAdapters = @(),
        $UserGpuPreference = $null,
        [string[]]$ExecutablePaths = @()
    )

    if ($null -eq $UserGpuPreference) {
        $UserGpuPreference = Get-ODSWindowsUserGpuPreference -ExecutablePaths $ExecutablePaths
    }

    if ($UserGpuPreference.PreferDiscreteGpu) {
        return [pscustomobject]@{
            GpuInfo           = $PrimaryGpuInfo
            ExecutionMode     = $(if ($PrimaryGpuInfo.Backend -ne 'none' -and $PrimaryGpuInfo.VramMB -gt 0) { 'gpu_discrete' } else { 'cpu' })
            PreferDiscreteGpu = $true
            PreferenceMode    = $UserGpuPreference.Mode
            PreferenceSource  = $UserGpuPreference.Source
        }
    }

    # User explicitly selected Power Saving (GpuPreference=1) in Windows Settings:
    # Look for an integrated GPU in AllAdapters, or fall back to CPU execution.
    $integrated = @($AllAdapters | Where-Object {
        $_.Name -match '(?i)intel.*(uhd|iris|hd graphics)|amd.*radeon\(tm\)\s+graphics' -and
        $_.Name -notmatch '(?i)nvidia|geforce|quadro|rtx|gtx|radeon\s+rx|intel.*arc'
    } | Select-Object -First 1)

    if ($integrated.Count -eq 1 -and [int]$integrated[0].VramMB -ge 2048) {
        $igpu = [pscustomobject]@{
            Backend       = $(if ($integrated[0].Name -match '(?i)amd|radeon') { 'amd' } else { 'intel' })
            Name          = [string]$integrated[0].Name
            VramMB        = [int]$integrated[0].VramMB
            DriverVersion = [string]$integrated[0].DriverVersion
            DriverMajor   = 0
            MemoryType    = 'unified'
            Count         = 1
            DeviceId      = ''
            ComputeCap    = ''
            IsBlackwell   = $false
        }
        return [pscustomobject]@{
            GpuInfo           = $igpu
            ExecutionMode     = 'gpu_integrated'
            PreferDiscreteGpu = $false
            PreferenceMode    = $UserGpuPreference.Mode
            PreferenceSource  = $UserGpuPreference.Source
        }
    }

    $cpuFallback = [pscustomobject]@{
        Backend       = 'none'
        Name          = $(if ($integrated.Count -eq 1) { [string]$integrated[0].Name } else { 'CPU (Windows Power Saving Mode)' })
        VramMB        = 0
        DriverVersion = ''
        DriverMajor   = 0
        MemoryType    = 'none'
        Count         = 0
        DeviceId      = ''
        ComputeCap    = ''
        IsBlackwell   = $false
    }
    return [pscustomobject]@{
        GpuInfo           = $cpuFallback
        ExecutionMode     = 'cpu_power_saving'
        PreferDiscreteGpu = $false
        PreferenceMode    = $UserGpuPreference.Mode
        PreferenceSource  = $UserGpuPreference.Source
    }
}

function Select-ODSWindowsDesktopLlamaDevice {
    <#
    .SYNOPSIS
        Choose the Vulkan or CUDA device for Windows Native Desktop across
        NVIDIA, AMD, and Intel GPUs while honoring UserGpuPreferences.
    .DESCRIPTION
        Unlike the WSL-Portal AMD-only device selector, Windows Native Desktop
        drives NVIDIA (GeForce/RTX/Quadro), AMD (Radeon), and Intel (Arc/Iris/UHD)
        GPUs natively via Vulkan or CUDA. Software rasterizers (llvmpipe,
        swiftshader, lavapipe, Microsoft Basic Render Driver) are never chosen.
        When PreferDiscreteGpu is $false (Power Saving mode in Windows Graphics
        Settings), discrete GPUs are skipped in favor of an integrated GPU or
        $null (explicit CPU route).
    #>
    param(
        [object[]]$Devices,
        [string]$AdapterName = '',
        [ValidateSet('vulkan', 'cuda', IgnoreCase = $false)][string]$Backend = 'vulkan',
        [bool]$PreferDiscreteGpu = $true
    )

    if (-not $PreferDiscreteGpu) {
        $igpuDevices = @($Devices | Where-Object {
            $_.Name -cmatch '^(Vulkan|CUDA)[0-9]{1,2}$' -and
            $_.Description -notmatch '(?i)llvmpipe|swiftshader|lavapipe|software|basic render|microsoft' -and
            $_.Description -match '(?i)uhd graphics|iris|hd graphics|radeon\(tm\)\s+graphics' -and
            $_.Description -notmatch '(?i)nvidia|geforce|quadro|rtx|gtx|radeon\s+rx|intel.*arc'
        })
        if ($igpuDevices.Count -ge 1 -and [long]$igpuDevices[0].TotalMiB -ge 2048) {
            return $igpuDevices[0]
        }
        return $null
    }

    if ($Backend -ceq 'cuda') {
        return (Select-ODSNativeLlamaCudaDevice -Devices $Devices -AdapterName $AdapterName)
    }

    $hardwareVulkan = @($Devices | Where-Object {
        $_.Name -cmatch '^Vulkan[0-9]{1,2}$' -and
        $_.Description -notmatch '(?i)llvmpipe|swiftshader|lavapipe|software|basic render|microsoft'
    })
    if ($hardwareVulkan.Count -eq 0) { return $null }

    $target = ConvertTo-ODSNativeLlamaAdapterKey $AdapterName
    if ($target) {
        $exact = @($hardwareVulkan | Where-Object { (ConvertTo-ODSNativeLlamaAdapterKey $_.Description) -eq $target })
        if ($exact.Count -ge 1) { return $exact[0] }
        $partial = @($hardwareVulkan | Where-Object {
            $key = ConvertTo-ODSNativeLlamaAdapterKey $_.Description
            $key -and ($key.Contains($target) -or $target.Contains($key))
        })
        if ($partial.Count -ge 1) { return $partial[0] }
    }

    # Prefer discrete GPUs (NVIDIA, AMD Radeon RX, Intel Arc) sorted by highest TotalMiB
    $discrete = @($hardwareVulkan | Where-Object {
        $_.Description -match '(?i)nvidia|geforce|quadro|rtx|gtx|\bamd\b|radeon|intel.*arc' -and
        $_.Description -notmatch '(?i)uhd graphics|hd graphics'
    } | Sort-Object -Property TotalMiB -Descending)
    if ($discrete.Count -ge 1) { return $discrete[0] }

    return ($hardwareVulkan | Sort-Object -Property TotalMiB -Descending | Select-Object -First 1)
}

function Test-ODSWindowsDesktopLlamaQualification {
    <#
    .SYNOPSIS
        Qualify llama-server.exe for Windows Native Desktop across NVIDIA, AMD,
        Intel, and CPU routes while honoring DirectX UserGpuPreferences.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][int]$ExpectedBuild,
        [string]$AdapterName = '',
        [bool]$PreferDiscreteGpu = $true,
        $CodeIntegrity = $null,
        [ValidateSet('vulkan', 'cuda', IgnoreCase = $false)][string]$Backend = 'vulkan',
        [ValidateSet('x64', 'arm64', IgnoreCase = $false)][string]$Architecture = 'x64'
    )

    $baseQual = Test-ODSNativeLlamaQualification -ExecutablePath $ExecutablePath -ExpectedBuild $ExpectedBuild `
        -AdapterName $AdapterName -CodeIntegrity $CodeIntegrity -Backend $Backend -Architecture $Architecture
    $desktopDevice = Select-ODSWindowsDesktopLlamaDevice -Devices $baseQual.Devices -AdapterName $AdapterName `
        -Backend $Backend -PreferDiscreteGpu $PreferDiscreteGpu

    return [pscustomobject]@{
        Build             = $baseQual.Build
        Commit            = $baseQual.Commit
        Devices           = $baseQual.Devices
        Device            = $desktopDevice
        PolicyHint        = $baseQual.PolicyHint
        Backend           = $Backend
        PreferDiscreteGpu = $PreferDiscreteGpu
    }
}

