# Elevated prerequisite helper. Never installs ODS or creates a Linux user.
$ErrorActionPreference = 'Stop'
try {
    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 19041) { throw 'Update Windows before enabling the supported WSL installation flow (Windows build 19041 or newer required).' }
    $dism = Join-Path ([Environment]::SystemDirectory) 'dism.exe'
    foreach ($feature in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
        & $dism /online /enable-feature "/featurename:$feature" /all /norestart
        if ($LASTEXITCODE -notin @(0, 3010)) { throw "Windows could not enable $feature (exit $LASTEXITCODE)." }
    }
    # Require an explicit resume even if DISM reports success without 3010.
    exit 3010
} catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
