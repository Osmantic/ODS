param([string]$SourceWindowsDir = '')
$ErrorActionPreference = 'Stop'
$windows = Join-Path $PSScriptRoot '../installers/windows'
if ($SourceWindowsDir) { $windows = $SourceWindowsDir }
. (Join-Path $windows 'lib/private-file.ps1')
$helper = Join-Path $windows 'lib/opencode-startup.ps1'
if (Test-Path -LiteralPath $helper) { . $helper }
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $windows 'phases/07-devtools.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Phase parse failed' }
$body = $ast.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($_ocReady -and')}, $true)
if (-not $body) { throw 'OpenCode installer block missing' }
$block = [scriptblock]::Create($body.Extent.Text)
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ods opencode startup ' + [guid]::NewGuid().ToString('N'))
$installDir = Join-Path $scratch ("owner's " + [char]0x00e9 + ' install')
$script:entry = Join-Path $scratch 'Startup/ods-opencode.vbs'
$script:OPENCODE_DIR = Join-Path $scratch 'opencode'
$script:OPENCODE_EXE = Join-Path $script:OPENCODE_DIR 'opencode.exe'
$script:OPENCODE_PORT = 3003
$script:OPENCODE_TASK_NAME = 'ODSOpenCodeWebFixture'
$gpuInfo = @{Backend='cpu'}; $tierConfig = @{GgufFile='fixture.gguf';LlmModel='fixture';MaxContext=4096}
$cloudMode = $false; $_ocReady = $true
$script:denied = $true; $script:startDenied = $false; $script:directStarts = 0; $script:taskStarts = 0; $script:checks = 0
$script:messages = [Collections.Generic.List[string]]::new()
$script:foreignOwner = $false
function Get-Acl {
    param($LiteralPath, $ErrorAction)
    $acl = Microsoft.PowerShell.Security\Get-Acl @PSBoundParameters
    if ($script:foreignOwner -and $LiteralPath -eq $script:entry) {
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    }
    return $acl
}
function Check($condition, $message) { if (-not $condition) { throw $message }; $script:checks++; Write-Host "PASS $message" }
function Get-ODSOpenCodeStartupPath { return $script:entry }
function Write-AI { param($Message) $script:messages.Add([string]$Message) }
function Write-AIWarn { param($Message) $script:messages.Add([string]$Message) }
function Write-AISuccess { param($Message) $script:messages.Add([string]$Message) }
function Sync-WindowsOpenCodeConfigFromEnv { param($InstallDir,$GpuBackend,$CloudMode,$DefaultModelId,$DefaultModelName,$DefaultContextLimit) return @{Status='created';ModelName='fixture'} }
function Stop-ScheduledTask { param($TaskName,$ErrorAction) }
function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction) }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) return @{} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn) return @{} }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit) return @{} }
function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) return @{} }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal,$Description,[switch]$Force,$ErrorAction) if ($script:denied) { throw [UnauthorizedAccessException]::new('Access denied fixture') } }
function Start-ScheduledTask { param($TaskName,$ErrorAction) if ($script:startDenied) { throw 'Task start denied fixture' }; $script:taskStarts++ }
function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$ErrorAction) $script:directStarts++ }
function Start-Sleep { param($Seconds) }
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) return [pscustomobject]@{OwningProcess=123} }
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) if ($Filter) { return [pscustomobject]@{ExecutablePath=$script:OPENCODE_EXE} }; return @() }

try {
    New-Item -ItemType Directory -Path $installDir,$script:OPENCODE_DIR,(Split-Path $script:entry) | Out-Null
    [IO.File]::WriteAllText($script:OPENCODE_EXE,'never executed')
    [IO.File]::WriteAllText((Join-Path $installDir 'ods.ps1'),'[pscustomobject]@{home=$env:ODS_HOME;arguments=@($args)} | ConvertTo-Json -Compress')
    . $block
    if (-not (Test-Path -LiteralPath $script:entry)) { $script:messages | Write-Host }
    Check (Test-Path -LiteralPath $script:entry) 'registration denial creates durable owner Startup fallback'
    Check ($script:directStarts -eq 1 -and $script:taskStarts -eq 0) 'denial still starts only the existing session launcher once'
    $content = [IO.File]::ReadAllText($script:entry)
    if (-not (Test-ODSOpenCodeStartupOwned -Path $script:entry -InstallDir $installDir)) {
        $observed = Get-Acl -LiteralPath $script:entry
        Write-Host ('Startup custody: owner={0}; current={1}; exactContent={2}; attributes={3}' -f
            $observed.GetOwner([Security.Principal.SecurityIdentifier]).Value,
            [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
            ($content -ceq (Get-ODSOpenCodeStartupContent -InstallDir $installDir)),
            (Get-Item -LiteralPath $script:entry).Attributes)
    }
    Check (Test-ODSOpenCodeStartupOwned -Path $script:entry -InstallDir $installDir) 'real private file is owned by this installation'
    $acl = Get-Acl -LiteralPath $script:entry
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Check ($acl.AreAccessRulesProtected -and $rules.Count -eq 1 -and $rules[0].IdentityReference -eq [Security.Principal.WindowsIdentity]::GetCurrent().User) 'Startup payload has the current-user-only DACL'
    $script:foreignOwner = $true
    Check (-not (Test-ODSOpenCodeStartupOwned -Path $script:entry -InstallDir $installDir)) 'an Administrators-owned existing entry is not treated as current-owner custody'
    Remove-ODSOpenCodeStartupFallback -InstallDir $installDir
    Check (Test-Path -LiteralPath $script:entry) 'foreign ownership is preserved even when its contents match'
    $script:foreignOwner = $false
    $encoded = [regex]::Match($content,'-EncodedCommand ([A-Za-z0-9+/=]+)').Groups[1].Value
    $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
    $previousHome = $env:ODS_HOME
    try { $env:ODS_HOME = 'foreign'; $actual = (& ([scriptblock]::Create($command)) | ConvertFrom-Json) }
    finally { $env:ODS_HOME = $previousHome }
    Check ($actual.home -ceq $installDir -and ($actual.arguments -join ',') -ceq 'start,opencode') 'generated command uses the exact custom Unicode/apostrophe installation and CLI arguments'
    $launcher = [IO.File]::ReadAllText((Join-Path $script:OPENCODE_DIR 'start-opencode.ps1'))
    Check ($launcher.Contains('web --port $opencodePort --hostname 127.0.0.1') -and $launcher.Contains('Get-WindowsODSEnvPort') -and $launcher.Contains('Remove-Item Env:OPENCODE_SERVER_PASSWORD')) 'fallback retains the configured-port loopback launcher and password handling'
    $stamp = (Get-Item -LiteralPath $script:entry).LastWriteTimeUtc
    . $block
    Check ([IO.File]::ReadAllText($script:entry) -ceq $content -and (Get-Item -LiteralPath $script:entry).LastWriteTimeUtc -eq $stamp) 'retained installer rerun reuses the exact entry without rewriting it'
    $script:denied = $false; $directBefore = $script:directStarts
    . $block
    Check (-not (Test-Path -LiteralPath $script:entry) -and $script:taskStarts -eq 1 -and $script:directStarts -eq $directBefore) 'successful task registration removes its exact fallback without a duplicate direct launch'
    $script:startDenied = $true
    . $block
    Check (-not (Test-Path -LiteralPath $script:entry) -and $script:directStarts -eq ($directBefore + 1)) 'a registered task whose immediate start fails does not gain duplicate login persistence'
    $script:startDenied = $false

    $foreign = Get-ODSOpenCodeStartupContent -InstallDir (Join-Path $scratch 'another install')
    Write-ODSPrivateEnvFile -Path $script:entry -Content $foreign
    $script:denied = $true
    . $block
    Check ([IO.File]::ReadAllText($script:entry) -ceq $foreign) 'registration denial preserves another installation startup entry'
    Check (@($script:messages | Where-Object { $_ -like 'OpenCode login persistence is unavailable; only this session*' }).Count -gt 0) 'unavailable persistence is reported separately from session startup'
    $script:denied = $false
    . $block
    Check ([IO.File]::ReadAllText($script:entry) -ceq $foreign) 'successful task registration also preserves a foreign fallback'
    Remove-Item -LiteralPath $script:entry
    Write-ODSPrivateEnvFile -Path $script:entry -Content ($content + 'WshShell.Run "calc.exe", 0, False')
    Remove-ODSOpenCodeStartupFallback -InstallDir $installDir
    Check (Test-Path -LiteralPath $script:entry) 'extra Startup commands cannot establish cleanup ownership'
    Remove-Item -LiteralPath $script:entry
    New-Item -ItemType Directory -Path $script:entry | Out-Null
    $refused = $false
    try { $null = Set-ODSOpenCodeStartupFallback -InstallDir $installDir } catch { $refused = $true }
    Check ($refused -and (Test-Path -LiteralPath $script:entry -PathType Container)) 'directory collision is preserved rather than recursively replaced'
    Remove-Item -LiteralPath $script:entry
    $refused = $false
    try { $null = Set-ODSOpenCodeStartupFallback -InstallDir (Join-Path $scratch 'missing install') } catch { $refused = $true }
    Check ($refused -and -not (Test-Path -LiteralPath $script:entry)) 'missing installed CLI cannot create a broken login entry'
    $null = Set-ODSOpenCodeStartupFallback -InstallDir $installDir

    # Run the actual uninstaller helper with only process enumeration and the
    # unrelated host-agent Startup path isolated; no product process is used.
    $cli = [Management.Automation.Language.Parser]::ParseFile((Join-Path $windows 'ods.ps1'),[ref]$tokens,[ref]$errors)
    $stop = $cli.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Stop-ODSUninstallOwnedHelpers'},$true)
    . ([scriptblock]::Create($stop.Extent.Text))
    function Test-Path {
        param($Path,$LiteralPath,$PathType)
        $p = if ($LiteralPath) { $LiteralPath } else { $Path }
        if ($p -eq (Join-Path ([Environment]::GetFolderPath('Startup')) 'ods-host-agent.vbs')) { return $false }
        if ($PathType) { return Microsoft.PowerShell.Management\Test-Path -LiteralPath $p -PathType $PathType }
        return Microsoft.PowerShell.Management\Test-Path -LiteralPath $p
    }
    Stop-ODSUninstallOwnedHelpers
    Check (-not (Test-Path -LiteralPath $script:entry)) 'actual uninstall helper removes only this installation fallback'
    Write-ODSPrivateEnvFile -Path $script:entry -Content $foreign
    Stop-ODSUninstallOwnedHelpers
    Check ([IO.File]::ReadAllText($script:entry) -ceq $foreign) 'actual uninstall helper preserves another installation fallback'
    Check (@(Get-ChildItem -LiteralPath (Split-Path $script:entry) -Filter '.ods-opencode-*').Count -eq 0) 'private publication staging files are settled'
    Write-Host "PASS $script:checks OpenCode Startup checks"
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
