# Windows Desktop owner settings survive reruns: an explicit value wins, then
# the saved one, then the default; $false and 0 are choices, not "unset". Only
# temporary files are written. Runs under Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$library = Join-Path $root 'installers/windows/lib/desktop-settings.ps1'
. (Join-Path $root 'installers/windows/lib/private-file.ps1')
. $library
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Write-AIWarn { param([string]$Message) }
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT

$defaults = [ordered]@{ ModelId = 'qwen3.5-2b-q4'; UiPort = 3101; StartAtSignIn = $true; ContextSize = 8192 }
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-settings-' + [guid]::NewGuid().ToString('N'))
$settingsPath = Join-Path (Join-Path $fixture 'Desktop [QA]') 'settings.json'
$null = New-Item -ItemType Directory -Path $fixture
try {
    # --- Precedence: explicit, then saved, then default ---
    $fresh = Resolve-ODSDesktopSettings -Defaults $defaults
    Check ($fresh.ModelId -eq 'qwen3.5-2b-q4' -and $fresh.UiPort -eq 3101 -and $fresh.StartAtSignIn -eq $true) 'a first install takes the defaults'
    $saved = [pscustomobject]@{ ModelId = 'gemma4-e4b-q4'; UiPort = 13101; StartAtSignIn = $false; ContextSize = 16384 }
    $rerun = Resolve-ODSDesktopSettings -Previous $saved -Defaults $defaults
    Check ($rerun.ModelId -eq 'gemma4-e4b-q4' -and $rerun.UiPort -eq 13101 -and $rerun.ContextSize -eq 16384) 'a rerun keeps every saved choice'
    Check ($rerun.StartAtSignIn -eq $false) 'a saved $false is kept, not reset to the default $true'
    $explicit = Resolve-ODSDesktopSettings -Explicit @{ UiPort = 3201 } -Previous $saved -Defaults $defaults
    Check ($explicit.UiPort -eq 3201 -and $explicit.ModelId -eq 'gemma4-e4b-q4') 'an explicit value wins for its key and the other saved choices stay'
    $off = Resolve-ODSDesktopSettings -Explicit @{ StartAtSignIn = $false } -Defaults $defaults
    Check ($off.StartAtSignIn -eq $false) 'an explicit $false is a choice, not "unset"'
    $blank = Resolve-ODSDesktopSettings -Explicit @{ ModelId = ''; UiPort = $null } -Previous $saved -Defaults $defaults
    Check ($blank.ModelId -eq 'gemma4-e4b-q4' -and $blank.UiPort -eq 13101) 'an empty or absent explicit value never erases a saved choice'
    $unknown = Resolve-ODSDesktopSettings -Previous ([pscustomobject]@{ ModelId = 'x'; Retired = 'old' }) -Defaults $defaults
    Check (-not $unknown.Contains('Retired') -and @($unknown.Keys).Count -eq 4) 'keys that are no longer settings are not carried forward'

    # --- Types: a hand edit with the wrong type stops setup with the key name ---
    $message = Get-Failure { $null = Resolve-ODSDesktopSettings -Previous ([pscustomobject]@{ UiPort = 'abc' }) -Defaults $defaults }
    Check ($message -match "'UiPort' must be a integer" -and $message -match 'saved') 'a saved value of the wrong type is refused with its key'
    $message = Get-Failure { $null = Resolve-ODSDesktopSettings -Explicit @{ StartAtSignIn = 'yes' } -Defaults $defaults }
    Check ($message -match "'StartAtSignIn' must be a boolean" -and $message -match 'explicit') 'an explicit value of the wrong type is refused'
    $message = Get-Failure { $null = Resolve-ODSDesktopSettings -Defaults ([ordered]@{ Bad = @(1, 2) }) }
    Check ($message -match 'must be a string, integer or boolean') 'a default of an unsupported type is a programming error'

    # --- Round trip: UTF-8 without a BOM, private, readable by the next run ---
    $named = Resolve-ODSDesktopSettings -Explicit @{ ModelId = ('mod' + [char]0x00E8 + 'le') } -Defaults $defaults
    Write-ODSDesktopSettings -Path $settingsPath -Settings $named
    $bytes = [IO.File]::ReadAllBytes($settingsPath)
    Check (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) 'settings.json is written without a BOM'
    $back = Read-ODSDesktopSettings $settingsPath
    $again = Resolve-ODSDesktopSettings -Previous $back -Defaults $defaults
    Check ($again.ModelId -ceq ('mod' + [char]0x00E8 + 'le') -and $again.UiPort -eq 3101 -and $again.StartAtSignIn -eq $true) 'a non-ASCII choice survives write, read and resolve'
    if ($onWindows) {
        $rules = @((Get-Acl -LiteralPath $settingsPath).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        Check ($rules.Count -eq 1 -and $rules[0].IdentityReference -eq [Security.Principal.WindowsIdentity]::GetCurrent().User) 'settings.json is readable only by the current user'
    }
    Check ($null -eq (Read-ODSDesktopSettings (Join-Path $fixture 'missing.json'))) 'a missing settings file means a first install'

    # --- Damaged files stop setup instead of silently resetting choices ---
    [IO.File]::WriteAllText($settingsPath, '{"ModelId":', [Text.UTF8Encoding]::new($false))
    $message = Get-Failure { $null = Read-ODSDesktopSettings $settingsPath }
    Check ($message -match 'not valid JSON' -and $message.Contains($settingsPath)) 'a torn settings file is reported with its path'
    [IO.File]::WriteAllText($settingsPath, '[1,2]', [Text.UTF8Encoding]::new($false))
    Check ((Get-Failure { $null = Read-ODSDesktopSettings $settingsPath }) -match 'must hold a JSON object') 'a settings file that is not an object is refused'
    [IO.File]::WriteAllBytes($settingsPath, [byte[]](0x7B, 0x22, 0xC3, 0x28, 0x22, 0x7D))
    Check ((Get-Failure { $null = Read-ODSDesktopSettings $settingsPath }) -ne '') 'invalid UTF-8 is refused, not decoded into a different choice'

    # --- Every path call in the library is literal ---
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($library, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) 'desktop-settings.ps1 parses without errors'
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($wildcard.Count -eq 0) 'every path cmdlet uses -LiteralPath'
    Check (-not ([IO.File]::ReadAllBytes($library) | Where-Object { $_ -gt 127 })) 'desktop-settings.ps1 is ASCII-only for Windows PowerShell 5.1'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop settings checks passed."
