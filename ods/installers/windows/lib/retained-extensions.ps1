function Add-ODSWindowsRetainedExtensions {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string[]]$ComposeFlags,
        [string]$Tier = '1',
        [string]$GpuBackend = 'none',
        [int]$GpuCount = 1,
        [string]$OdsMode = 'local'
    )
    $userRoot = Join-Path $InstallDir 'data/user-extensions'
    # Fresh installs need neither a shell nor extra Python dependencies here.
    if (-not (Test-Path -LiteralPath $userRoot -PathType Container) -or
        @(Get-ChildItem -LiteralPath $userRoot -Directory -Force -ErrorAction Stop |
            Where-Object { -not $_.Name.StartsWith('.') }).Count -eq 0) {
        return $ComposeFlags
    }
    $helper = Join-Path $InstallDir 'scripts/windows-retained-extensions.py'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
        throw 'Retained extension resolver is missing; repair the ODS installation before launching Compose'
    }
    $python = Resolve-ODSHostAgentPython
    if (-not $python) { throw 'Python 3 with PyYAML is required to retain installed extensions' }
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $arguments = @($python.PrefixArgs) + @('-X', 'utf8', $helper, '--install-dir', $InstallDir,
        '--tier', $Tier, '--backend', $GpuBackend, '--gpu-count', "$GpuCount", '--mode', $OdsMode)
    $inputJson = ConvertTo-Json -InputObject @($ComposeFlags) -Compress
    $output = $inputJson | & $python.FilePath @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Retained extensions require review before Compose launch: $(($output | Out-String).Trim())"
    }
    try { $resolved = ($output | Out-String) | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Retained extension resolver returned invalid Compose arguments' }
    return @($resolved)
}
