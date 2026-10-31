# WSL memory budget for a model that runs on Windows in shared memory
# (installers/windows/lib/wsl-memory-budget.ps1): the formula on measured and
# documented machines, the .wslconfig merge that never overrides the owner,
# the plain-words notices, and that nothing elevates or touches the page
# file. Windows' memory status is a fixture; WSL never runs and only
# temporary files are written.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-memory-budget.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
$crlf = "`r`n"

# --- The formula (pure) ----------------------------------------------------------
# RTX Spark as measured on 2026-10-25: 30.49 GiB visible, 96 GB page file,
# commit limit 126.49 GiB, a model server holding about 96 GB.
$spark = Get-ODSWslMemoryBudget -VisibleGiB 30.49 -CommitLimitGiB 126.49 -EngineCommitGiB 96
Check ($spark.CapGiB -eq 12 -and $spark.BelowFloor -and $spark.PageFileShortGiB -eq 6 -and [math]::Abs($spark.DefaultGiB - 15.245) -lt 0.01) 'the RTX Spark outage machine gets 12 GB, the least ODS needs, and a page-file note'
$decimal = Get-ODSWslMemoryBudget -VisibleGiB 30.49 -CommitLimitGiB 126.49 -EngineCommitGiB 89.41
Check ($decimal.CapGiB -eq 13 -and -not $decimal.BelowFloor) 'read as 96 decimal GB (89.4 GiB) the same machine gets 13 GB'
$stock = Get-ODSWslMemoryBudget -VisibleGiB 30.49 -CommitLimitGiB 126.49 -EngineCommitGiB 25.8
Check ($null -eq $stock.CapGiB -and -not $stock.BelowFloor) 'a 26 GB llama.cpp model on that page file leaves WSL its default'
$factory = Get-ODSWslMemoryBudget -VisibleGiB 62.5 -CommitLimitGiB 62.5 -EngineCommitGiB 25.8
Check ($factory.CapGiB -eq 12 -and $factory.RoomGiB -eq 12.7) 'a factory RTX Spark (no page file) limits WSL to 12 GB instead of 31'
$apuShared = Get-ODSWslMemoryBudget -VisibleGiB 127.5 -CommitLimitGiB 143.5 -EngineCommitGiB 24
Check ($null -eq $apuShared.CapGiB) 'a 128 GB Strix Halo with a small carve-out keeps WSL''s default for a 24 GB model'
$apuBig = Get-ODSWslMemoryBudget -VisibleGiB 127.5 -CommitLimitGiB 143.5 -EngineCommitGiB 72
Check ($apuBig.CapGiB -eq 47) 'a 72 GB model in shared memory on that Strix Halo limits WSL to 47 GB'
$apuCarve = Get-ODSWslMemoryBudget -VisibleGiB 31.6 -CommitLimitGiB 36.6 -EngineCommitGiB 0
Check ($apuCarve.CapGiB -eq 12) 'with the model in a 96 GB carve-out WSL still keeps Windows its reserve and headroom'
$small = Get-ODSWslMemoryBudget -VisibleGiB 20 -CommitLimitGiB 24 -EngineCommitGiB 6
Check ($null -eq $small.CapGiB -and $small.BelowFloor) 'ODS never raises a default below 12 GB to 12 GB'
Check ($script:ODSWslBudgetWindowsReserveGiB -eq 16 -and $script:ODSWslBudgetHeadroomGiB -eq 8 -and $script:ODSWslMemoryFloorGiB -eq 12) 'reserve 16 GiB, headroom 8 GiB, floor 12 GB'
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
Check ($script:ODSWslBudgetWindowsReserveGiB -eq $script:ODSNativeWindowsReserveGiB) 'the budget keeps the same Windows reserve as the launcher''s commit rule'

# --- autoMemoryReclaim needs WSL 2.0.0 ----------------------------------------------
Check ((Test-ODSWslReclaimSupported "WSL version: 2.6.1.0${crlf}Kernel version: 6.6.87.2-1") -and
    (Test-ODSWslReclaimSupported "Versao do WSL: 2.0.14.0${crlf}Versao do kernel: 5.15.133.1-1")) 'WSL 2.x supports autoMemoryReclaim, whatever the language'
Check (-not (Test-ODSWslReclaimSupported "WSL version: 1.2.5.0${crlf}Kernel version: 5.15.90.1") -and -not (Test-ODSWslReclaimSupported '') -and
    -not (Test-ODSWslReclaimSupported 'Invalid command line option: --version')) 'older or inbox WSL gets no autoMemoryReclaim'

# --- Invoke-ODSPortalWslMemoryBudget against a temporary profile ---------------------
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-wsl-budget-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previous = @{ LOCALAPPDATA = $env:LOCALAPPDATA; USERPROFILE = $env:USERPROFILE }
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
$env:USERPROFILE = Join-Path $fixture 'Profile'
$null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:USERPROFILE -Force
$path = Join-Path $env:USERPROFILE '.wslconfig'
$wsl2 = "WSL version: 2.6.1.0${crlf}Kernel version: 6.6.87.2-1"
$script:status = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommitAvailableGiB = 4.2 }
function Get-ODSWindowsMemoryStatus { return $script:status }
function Invoke-Budget([double]$Engine = 96, [string]$Version = $wsl2) {
    $script:said = @(& { $script:budget = Invoke-ODSPortalWslMemoryBudget -EngineCommitGiB $Engine -Engine 'ODS-Inference' -WslVersionText $Version } 6>&1 |
        ForEach-Object { [string]$_ }) -join "`n"
    return $script:budget
}
function Clear-Fixture {
    foreach ($item in @($path, (Get-ODSWindowsSettingsPath))) { if (Test-Path -LiteralPath $item) { Remove-Item -LiteralPath $item -Force } }
}
function Get-Backups { return @(Get-ChildItem -LiteralPath (Get-ODSWindowsSettingsDir) -Filter 'wslconfig-backup-*' -ErrorAction SilentlyContinue) }
try {
    $first = Invoke-Budget
    $text = [IO.File]::ReadAllText($path)
    Check ($first.Result -ceq 'written' -and $first.RestartNeeded -and $text.Contains("memory=12GB") -and $text.Contains('autoMemoryReclaim=dropcache') -and
        $text.Contains('[wsl2]') -and $text.Contains('[experimental]')) 'no .wslconfig: ODS writes memory=12GB and autoMemoryReclaim=dropcache'
    Check ($script:said -match 'ODS set memory=12GB under \[wsl2\]' -and $script:said -match 'autoMemoryReclaim=dropcache under \[experimental\]' -and
        $script:said -match 'To undo it, run' -and $script:said -match '126\.5 GiB in total' -and $script:said -match 'about 96 GiB') 'setup says what it changed, why, and how to undo it'
    Check ($script:said -match 'ODS does not change the page file; that is your call' -and $script:said -match 'about 6 GB larger') 'a page file too small for both is a recommendation only'
    Check ((Get-ODSWslRestartNotice) -match 'only after it restarts' -and (Get-ODSWslRestartNotice) -match 'wsl --shutdown' -and
        (Get-ODSWslRestartNotice) -match 'start Docker Desktop again') 'the restart notice says in plain words how WSL picks it up'

    $again = Invoke-Budget
    Check ($again.Result -ceq 'unchanged' -and -not $again.RestartNeeded -and (Get-Backups).Count -eq 0 -and [IO.File]::ReadAllText($path) -ceq $text -and
        $script:said -notmatch 'ODS set') 'a rerun with the same figures writes nothing, not even a backup'

    $script:status = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49 + 7; CommitAvailableGiB = 11 }
    $bigger = Invoke-Budget
    $text = [IO.File]::ReadAllText($path)
    Check ($bigger.Result -ceq 'written' -and $text.Contains('memory=13GB') -and -not $text.Contains('memory=12GB') -and
        ([regex]::Matches($text, 'autoMemoryReclaim=')).Count -eq 1) 'a larger page file lets ODS raise its own limit in place'
    $script:status = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommitAvailableGiB = 4.2 }

    $owner = "# mine${crlf}[wsl2]${crlf}memory=20GB${crlf}processors=8${crlf}"
    [IO.File]::WriteAllText($path, $owner)
    Remove-Item -LiteralPath (Get-ODSWindowsSettingsPath) -Force
    $backups = (Get-Backups).Count
    $kept = Invoke-Budget
    $text = [IO.File]::ReadAllText($path)
    Check ($text.Contains('memory=20GB') -and -not $text.Contains('memory=12GB') -and $text.Contains('processors=8') -and $text.Contains('# mine') -and
        $text.Contains('autoMemoryReclaim=dropcache') -and $kept.Result -ceq 'written') 'the owner''s memory= and other settings stay; ODS adds only reclaim'
    Check ($script:said -match 'sets memory=20GB, so ODS left it' -and $script:said -match 'at most 12 GB' -and (Get-Backups).Count -eq $backups + 1) 'setup says the owner''s limit is above what this PC can spare, and backs up their file'

    [IO.File]::WriteAllText($path, "[wsl2]${crlf}memory=8GB${crlf}[experimental]${crlf}autoMemoryReclaim=gradual${crlf}")
    Remove-Item -LiteralPath (Get-ODSWindowsSettingsPath) -Force
    $quiet = Invoke-Budget
    Check ($quiet.Result -ceq 'owner' -and -not $quiet.RestartNeeded -and [IO.File]::ReadAllText($path) -ceq "[wsl2]${crlf}memory=8GB${crlf}[experimental]${crlf}autoMemoryReclaim=gradual${crlf}" -and
        $script:said -notmatch 'ODS left it') 'an owner limit within the budget and their own reclaim are left alone, silently'

    Clear-Fixture
    $old = Invoke-Budget -Version "WSL version: 1.2.5.0${crlf}Kernel version: 5.15.90.1"
    Check ($old.Result -ceq 'written' -and [IO.File]::ReadAllText($path).Contains('memory=12GB') -and -not [IO.File]::ReadAllText($path).Contains('experimental')) 'an older WSL gets the limit without autoMemoryReclaim'

    Clear-Fixture
    $fits = Invoke-Budget -Engine 20
    Check ($null -eq $fits.Budget.CapGiB -and -not ([IO.File]::ReadAllText($path).Contains('memory=')) -and [IO.File]::ReadAllText($path).Contains('autoMemoryReclaim=dropcache')) 'when WSL''s default fits, only reclaim is set'

    [IO.File]::WriteAllBytes($path, [byte[]](@(0xFF, 0xFE) + [Text.Encoding]::Unicode.GetBytes("[wsl2]${crlf}processors=8${crlf}")))
    $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
    $unsupported = Invoke-Budget
    Check ($unsupported.Result -ceq 'unsupported' -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq $before -and
        $script:said -match 'not a plain text file ODS can edit safely' -and $script:said -match 'memory=12GB') 'a UTF-16 .wslconfig is never edited; setup says what to add by hand'

    $script:status = $null
    Clear-Fixture
    $unavailable = Invoke-Budget
    Check ($unavailable.Result -ceq 'unavailable' -and -not (Test-Path -LiteralPath $path) -and $script:said -match 'not changed') 'without Windows'' memory figures nothing is written'
} finally {
    foreach ($name in $previous.Keys) { Set-Item -Path "Env:\$name" -Value $previous[$name] }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

# --- Never elevates, never changes a system setting --------------------------------
$source = [IO.File]::ReadAllText((Join-Path $root 'installers/windows/lib/wsl-memory-budget.ps1'))
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
$commands = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
Check ($errors.Count -eq 0 -and -not ($commands | Where-Object { $_ -match '^(Start-Process|Set-ItemProperty|New-ItemProperty|Set-ODSSparkPageFile|Start-ODSElevatedProcess|Invoke-ODSPortalWsl)$' }) -and
    $source -notmatch 'RunAs|runas|pagefile\.sys') 'the budget never elevates, never runs WSL and never touches the page file'
Check ($source.Contains('ullAvailPageFile') -and $source.Contains('GlobalMemoryStatusEx') -and $source.Contains('TotalPageFile')) 'Windows'' figures come from GlobalMemoryStatusEx, read unelevated'

# --- AMD: what a model takes from Windows' own memory -------------------------------
. (Join-Path $root 'installers/windows/lib/wsl-portal-amd.ps1')
$models = Join-Path ([IO.Path]::GetTempPath()) ('ods-amd-share-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $models
try {
    [IO.File]::WriteAllBytes((Join-Path $models 'big-00001-of-00002.gguf'), [byte[]]::new(3MB))
    [IO.File]::WriteAllBytes((Join-Path $models 'big-00002-of-00002.gguf'), [byte[]]::new(1MB))
    [IO.File]::WriteAllBytes((Join-Path $models 'mmproj.gguf'), [byte[]]::new(1MB))
    $selection = [pscustomobject]@{ GgufFile = 'big-00001-of-00002.gguf'; MmprojFile = 'mmproj.gguf' }
    $discrete = Get-ODSPortalAmdMemoryShare ([pscustomobject]@{ MemoryType = 'discrete' }) ([pscustomobject]@{ TotalMiB = 16304 }) $selection $models
    Check (-not $discrete.SharesSystemMemory) 'a discrete AMD GPU keeps its model in its own memory'
    $smallCarve = Get-ODSPortalAmdMemoryShare ([pscustomobject]@{ MemoryType = 'unified' }) ([pscustomobject]@{ TotalMiB = 512 }) $selection $models
    Check ($smallCarve.SharesSystemMemory -and [math]::Abs($smallCarve.EngineCommitGiB - (5MB / 1GB + 2 - 0.5)) -lt 0.01 -and
        $smallCarve.Engine -match 'big-00001-of-00002\.gguf') 'a Strix Halo with a small carve-out counts every split part, the projector and 2 GiB, less the carve-out'
    $bigCarve = Get-ODSPortalAmdMemoryShare ([pscustomobject]@{ MemoryType = 'unified' }) ([pscustomobject]@{ TotalMiB = 98304 }) $selection $models
    Check ($bigCarve.SharesSystemMemory -and $bigCarve.EngineCommitGiB -eq 0) 'a model inside a 96 GB carve-out takes nothing from Windows'' own memory'
} finally {
    Remove-Item -LiteralPath $models -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows WSL memory budget contracts."
