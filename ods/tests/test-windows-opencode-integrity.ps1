param([string]$ReviewedArchive)
$ErrorActionPreference = 'Stop'
$odsRoot = Split-Path -Parent $PSScriptRoot
$constants = Get-Content -LiteralPath (Join-Path $odsRoot 'installers/windows/lib/constants.ps1') -Raw
foreach ($line in [regex]::Matches($constants, '(?m)^\$script:OPENCODE_(VERSION|ZIP|URL)\s*=.*$')) {
    Invoke-Expression $line.Value
}
if ($script:OPENCODE_VERSION -ne '1.2.18' -or $script:OPENCODE_ZIP -ne 'opencode-windows-x64-baseline.zip') {
    throw 'Windows must select the reviewed baseline release for x64 CPUs without AVX2'
}
if ($script:OPENCODE_URL -ne 'https://github.com/anomalyco/opencode/releases/download/v1.2.18/opencode-windows-x64-baseline.zip') {
    throw 'Windows baseline URL does not match the reviewed release'
}
$phase = Get-Content -LiteralPath (Join-Path $odsRoot 'installers/windows/phases/07-devtools.ps1') -Raw
$start = $phase.IndexOf('if (-not (Test-Path $script:OPENCODE_EXE)) {')
$end = $phase.IndexOf("`nif (Test-Path " + '$script:OPENCODE_EXE) {', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Cannot locate the isolated OpenCode installation block' }
$block = $phase.Substring($start, $end - $start)
$originalTemp = $env:TEMP
$testDirectory = Join-Path $originalTemp ('ods-opencode-integrity-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
try {
    $env:TEMP = $testDirectory
    $script:OPENCODE_EXE = Join-Path $testDirectory 'bin/opencode.exe'
    $script:OPENCODE_BIN = Join-Path $testDirectory 'bin'
    [IO.File]::WriteAllText((Join-Path $testDirectory $script:OPENCODE_ZIP), 'substituted archive bytes')
    function Write-AI { param($Message) }
    function Write-AIWarn { param($Message) }
    function Write-AISuccess { param($Message) }
    function Invoke-DownloadWithRetry { throw 'A cached archive should reach the checksum guard' }
    function Test-ZipIntegrity { throw 'Tampered bytes reached ZIP parsing' }
    function Invoke-ExtractionWithRetry { throw 'Tampered bytes reached extraction' }
    Invoke-Expression $block
    if (Test-Path $script:OPENCODE_EXE) { throw 'An executable was installed from tampered bytes' }
    if (Test-Path (Join-Path $testDirectory $script:OPENCODE_ZIP)) { throw 'Poisoned cached archive was retained' }
    Write-Output 'PASS: substituted OpenCode bytes are rejected before ZIP parsing or extraction'

    if ($ReviewedArchive) {
        # Optional release qualification: use downloaded, unmodified publisher bytes.
        # The binary is extracted only into this fixture directory and never run.
        Copy-Item -LiteralPath $ReviewedArchive -Destination (Join-Path $testDirectory $script:OPENCODE_ZIP)
        function Test-ZipIntegrity {
            param($Path)
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [IO.Compression.ZipFile]::OpenRead($Path)
            try {
                if (@($archive.Entries | Where-Object FullName -eq 'opencode.exe').Count -ne 1) {
                    throw 'Reviewed archive does not have its executable at the root'
                }
            } finally { $archive.Dispose() }
            [pscustomobject]@{ Valid = $true }
        }
        function Invoke-ExtractionWithRetry {
            param($ZipPath, $DestinationPath)
            Expand-Archive -LiteralPath $ZipPath -DestinationPath $DestinationPath
            return $true
        }
        Invoke-Expression $block
        if (-not (Test-Path -LiteralPath $script:OPENCODE_EXE)) { throw 'Reviewed baseline executable was not extracted' }
        Write-Output 'PASS: real baseline ZIP passed the checksum guard and extracted into the isolated fixture'
    }
} finally {
    $env:TEMP = $originalTemp
    $resolved = [IO.Path]::GetFullPath($testDirectory)
    $allowedPrefix = [IO.Path]::GetFullPath($originalTemp).TrimEnd('\') + '\ods-opencode-integrity-'
    if (-not $resolved.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
