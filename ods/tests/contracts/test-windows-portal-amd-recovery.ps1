# Real AMD helpers with fixture-only task, process, socket and HTTP boundaries.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-amd.ps1')
$script:checks = 0
function Assert-Recovery([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

foreach ($version in @('10.0.0', '10.6.4', '10.7.0', '10.12.1', '2026.40.0.0')) {
    Assert-ODSPortalLemonadeVersion ([version]$version)
    Assert-Recovery $true "supported Lemonade $version retains its versioned launch contract"
}
foreach ($version in @('9.9.9', '11.0.0', '2026.41.0')) {
    $message = ''
    try { Assert-ODSPortalLemonadeVersion ([version]$version) } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'outside the supported Portal runtime contract') "unsupported Lemonade $version is refused before mutation"
}

# A reserved port has no listener. No real socket is bound by this test.
$savedPort = $env:AMD_INFERENCE_PORT
$env:AMD_INFERENCE_PORT = ''
$script:blockedPorts = @(8080, 13305)
function Get-ODSPortalPortOwner([int]$Port) { return $null }
function Test-ODSPortalPortBindable([int]$Port) { return $Port -notin $script:blockedPorts }
try {
    Assert-Recovery ((Select-ODSPortalLemonadePort) -eq 8000) 'reserved ports without listeners are skipped'
    $env:AMD_INFERENCE_PORT = '8080'
    $message = ''
    try { $null = Select-ODSPortalLemonadePort } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'cannot bind Windows loopback') 'an explicitly reserved port fails with an actionable message'
    $env:AMD_INFERENCE_PORT = '65536'
    $message = ''
    try { $null = Select-ODSPortalLemonadePort } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'between 1 and 65535') 'invalid explicit ports fail before probing'
} finally { $env:AMD_INFERENCE_PORT = $savedPort }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-amd-recovery-' + [guid]::NewGuid().ToString('N'))
$previousLocal = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
$null = New-Item -ItemType Directory -Path $fixture
try {
    $script:currentSid = 'S-1-5-21-100-200-300-1001'
    function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return $script:currentSid }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        function Get-Command { param($Name, $CommandType, $ErrorAction)
            if ($CommandType -eq 'Application') { return [pscustomobject]@{ Source = '/fixture/pwsh.exe'; Name = 'pwsh.exe' } }
            Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction Stop
        }
    }
    $firstName = Get-ODSPortalLemonadeTaskName
    $script:currentSid = 'S-1-5-21-100-200-300-1002'
    Assert-Recovery ((Get-ODSPortalLemonadeTaskName) -ne $firstName) 'different Windows SIDs have different task names'
    $script:currentSid = 'S-1-5-21-100-200-300-1001'
    $script:tasks = @{}
    $script:unreadableTask = ''
    $script:events = [Collections.Generic.List[string]]::new()
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        if ($TaskName -eq $script:unreadableTask) {
            $errorRecord = [Management.Automation.ErrorRecord]::new([UnauthorizedAccessException]::new('fixture task access denied'), 'denied', [Management.Automation.ErrorCategory]::PermissionDenied, $TaskName)
            Set-Variable -Name $ErrorVariable -Value @($errorRecord) -Scope 1
            return
        }
        if ($script:tasks.ContainsKey($TaskName)) { return $script:tasks[$TaskName] }
    }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
    }
    function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) @{ User = $User } }
    function New-ScheduledTaskSettingsSet {
        param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $ExecutionTimeLimit, $RestartCount, $RestartInterval)
        Assert-Recovery ($RestartCount -eq 3 -and $RestartInterval.TotalSeconds -eq 60) 'startup retry is bounded to three attempts at one-minute intervals'
        return @{ RestartCount = $RestartCount }
    }
    function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel) @{ UserId = $script:currentSid } }
    function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
        $script:events.Add('register:' + $TaskName)
        $script:tasks[$TaskName] = [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; Principal = $Principal; Actions = @($Action); State = 'Ready' }
    }
    function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add('disable:' + $TaskName) }
    function Start-ScheduledTask { param($TaskName, $TaskPath)
        Assert-Recovery ($TaskName -eq (Get-ODSPortalLemonadeTaskName)) 'registration starts only this Windows user task'
        $script:events.Add('start:' + $TaskName)
    }
    function Unregister-ScheduledTask { param($TaskName, $TaskPath, [switch]$Confirm, $ErrorAction)
        $script:events.Add('remove:' + $TaskName); $script:tasks.Remove($TaskName)
    }
    function Get-ODSPortalTaskEngineId { param($TaskName) return 0 }
    function Get-CimInstance { param($ClassName, $ErrorAction) return @() }
    function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) return @() }
    function Stop-ScheduledTask { throw 'No fixture task process is running' }
    $exe = Join-Path $fixture 'LemonadeServer.exe'
    [IO.File]::WriteAllText($exe, 'fixture; never executed')
    $contract = [pscustomobject]@{ ExecutablePath = $exe; Port = 13305; ModelsDir = (Join-Path (Get-ODSPortalStateDir) 'models'); ContextSize = 65536 }
    $legacyAction = (New-ODSPortalLemonadeRuntimeAction $contract 'Model.gguf').Action
    $legacy = [pscustomobject]@{ TaskName = 'ODSLemonadeRuntime'; TaskPath = '\'; Principal = @{ UserId = $script:currentSid }; Actions = @($legacyAction); State = 'Ready' }
    $script:tasks['ODSLemonadeRuntime'] = $legacy
    $null = Register-ODSPortalLemonadeTask $contract 'Model.gguf' 'Ubuntu-24.04' '/home/user/ods'
    Assert-Recovery (-not $script:tasks.ContainsKey('ODSLemonadeRuntime') -and $script:tasks.ContainsKey($firstName)) 'owned legacy task is adopted only after real action and plan validation'
    Assert-Recovery (($script:events -join ',') -eq "disable:ODSLemonadeRuntime,register:$firstName,remove:ODSLemonadeRuntime,start:$firstName") 'legacy migration disables the old trigger before publishing and removes it before starting'

    $script:tasks.Clear(); $script:events.Clear()
    $legacy.Principal.UserId = 'S-1-5-21-100-200-300-1002'
    $script:tasks['ODSLemonadeRuntime'] = $legacy
    $null = Register-ODSPortalLemonadeTask $contract 'Model.gguf'
    Assert-Recovery ($script:tasks.ContainsKey('ODSLemonadeRuntime') -and ($script:events -join ',') -eq "register:$firstName,start:$firstName") 'another account legacy task is preserved while creating this user task'

    $script:tasks.Remove($firstName); $script:events.Clear()
    $script:unreadableTask = 'ODSLemonadeRuntime'
    $null = Register-ODSPortalLemonadeTask $contract 'Model.gguf'
    Assert-Recovery ($script:tasks.ContainsKey('ODSLemonadeRuntime') -and ($script:events -join ',') -eq "register:$firstName,start:$firstName") 'an unreadable legacy task cannot block this user and is never disabled or removed'
    $script:unreadableTask = $firstName
    $message = ''
    try { $null = Get-ODSPortalLemonadeTask } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'Cannot inspect') 'an unreadable task bearing our SID still fails closed'
    $script:unreadableTask = ''

    $script:tasks.Clear(); $script:events.Clear()
    $legacy.Principal.UserId = $script:currentSid
    $legacy.Actions = @([pscustomobject]@{ Execute = 'unrelated.exe'; Arguments = ''; WorkingDirectory = $fixture })
    $script:tasks['ODSLemonadeRuntime'] = $legacy
    $message = ''
    try { $null = Register-ODSPortalLemonadeTask $contract 'Model.gguf' } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'not recognized' -and $script:events.Count -eq 0) 'same SID alone cannot authorize adoption of a foreign legacy action'

    $intentPath = Join-Path (Join-Path (Get-ODSPortalStateDir) 'portal-runtime') 'intent.json'
    Set-ODSPortalLemonadeIntent 'stopped'
    Assert-Recovery (-not (Test-ODSPortalLemonadeWanted $intentPath)) 'a durable deliberate stop prevents automatic restart'
    Set-ODSPortalLemonadeIntent 'running'
    Assert-Recovery (Test-ODSPortalLemonadeWanted $intentPath) 'an explicit start re-arms the durable launch intent'
    [IO.File]::WriteAllText($intentPath, '{"State":"broken"}')
    $message = ''
    try { $null = Test-ODSPortalLemonadeWanted $intentPath } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'intent is invalid') 'invalid intent fails closed'

    # The request boundary receives bytes, avoiding PS5.1's ASCII string body.
    $script:unicodeName = 'Model-' + [char]0x00E9 + '-' + [char]0x4E2D
    $script:unicodeDir = Join-Path $fixture ('models-' + [char]0x4E2D)
    function Invoke-RestMethod { param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, $ErrorAction)
        if ($Method -eq 'Post') {
            Assert-Recovery ($Body -is [byte[]] -and $ContentType -eq 'application/json; charset=utf-8') 'Lemonade JSON POST uses explicit UTF-8 bytes'
            $payload = [Text.UTF8Encoding]::new($false, $true).GetString($Body) | ConvertFrom-Json
            if ($Uri.EndsWith('/load')) {
                Assert-Recovery ($payload.model_name -ceq $script:unicodeName) 'Unicode model identity survives the HTTP request boundary'
            } else {
                Assert-Recovery ($payload.extra_models_dir -ceq $script:unicodeDir) 'Unicode model directory survives the HTTP request boundary'
            }
            return @{ status = 'success' }
        }
        if ($Uri.EndsWith('/config')) { return @{ extra_models_dir = $script:unicodeDir; llamacpp = @{ backend = 'vulkan' }; ctx_size = 65536 } }
        return @{ all_models_loaded = @(@{ model_name = $script:unicodeName; recipe_options = @{ ctx_size = 65536 } }) }
    }
    $null = Set-ODSLemonadeModernRuntimeConfig -Port 13305 -ModelsDir $script:unicodeDir -ContextSize 65536
    Set-ODSLemonadeLoadedModel -Port 13305 -ModelId $script:unicodeName -ContextSize 65536
} finally {
    $env:LOCALAPPDATA = $previousLocal
    if (-not ([IO.Path]::GetFullPath($fixture)).StartsWith(([IO.Path]::GetFullPath([IO.Path]::GetTempPath())), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Fixture cleanup escaped its temporary directory.'
    }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows Portal AMD recovery contracts."
