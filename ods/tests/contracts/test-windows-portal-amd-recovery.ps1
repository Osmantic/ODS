# Real AMD helpers with fixture-only task, process and socket boundaries.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-amd.ps1')
$script:checks = 0
function Assert-Recovery([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

# Ports. A reserved port has no listener. No real socket is bound here.
$savedPort = $env:AMD_INFERENCE_PORT
$env:AMD_INFERENCE_PORT = ''
$script:blockedPorts = @(8080)
$script:owners = @{}
function Get-ODSPortalPortOwner([int]$Port) { return $script:owners[$Port] }
function Test-ODSPortalPortBindable([int]$Port) { return $Port -notin $script:blockedPorts }
try {
    Assert-Recovery ((Select-ODSPortalRuntimePort) -eq 18080) 'a reserved 8080 without a listener moves a new install to 18080'
    $script:blockedPorts = @(8080, 18080, 28080)
    $message = ''
    try { $null = Select-ODSPortalRuntimePort } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match '8080 .+18080 .+28080' -and $message -notmatch '13305|8000\b' -and $message -match 'AMD_INFERENCE_PORT') 'a new install never squats Lemonade''s own ports 13305 and 8000'
    $script:blockedPorts = @()
    $script:owners = @{ 8080 = 'LemonadeServer' }
    Assert-Recovery ((Select-ODSPortalRuntimePort) -eq 18080) 'a user Lemonade on 8080 keeps its port; ODS takes the next quiet one'
    $script:owners = @{}
    $env:AMD_INFERENCE_PORT = '8080'
    $script:blockedPorts = @(8080)
    $message = ''
    try { $null = Select-ODSPortalRuntimePort } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'cannot bind Windows loopback') 'an explicitly reserved port fails with an actionable message'
    $env:AMD_INFERENCE_PORT = '65536'
    $message = ''
    try { $null = Select-ODSPortalRuntimePort } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'between 1 and 65535') 'invalid explicit ports fail before probing'
    $script:blockedPorts = @(13305)
    $message = ''
    try { Assert-ODSPortalPortAvailable 13305 } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'reserved after a restart' -and $message -match 'AMD_INFERENCE_PORT') 'a kept migration port that became reserved is explained'
} finally { $env:AMD_INFERENCE_PORT = $savedPort }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-amd-recovery-' + [guid]::NewGuid().ToString('N'))
$previousLocal = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $fixture
$null = New-Item -ItemType Directory -Path $fixture
try {
    $script:currentSid = 'S-1-5-21-100-200-300-1001'
    function Get-ODSPortalUserSid([string]$UserId) { if ($UserId) { return $UserId }; return $script:currentSid }
    function Get-Command { param($Name, $CommandType, $ErrorAction)
        if ($CommandType -eq 'Application') { return [pscustomobject]@{ Source = 'C:\fixture\powershell.exe'; Name = 'powershell.exe' } }
        Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction Stop
    }
    $firstName = Get-ODSPortalRuntimeTaskName
    Assert-Recovery ($firstName -ceq 'ODSLlamaServerRuntime-S-1-5-21-100-200-300-1001') 'the llama.cpp task is named ODSLlamaServerRuntime-<SID>'
    $script:currentSid = 'S-1-5-21-100-200-300-1002'
    Assert-Recovery ((Get-ODSPortalRuntimeTaskName) -ne $firstName) 'different Windows SIDs have different task names'
    $script:currentSid = 'S-1-5-21-100-200-300-1001'
    $script:tasks = @{}
    $script:unreadableTask = ''
    $script:queried = [Collections.Generic.List[string]]::new()
    $script:events = [Collections.Generic.List[string]]::new()
    function Get-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction, $ErrorVariable)
        $script:queried.Add($TaskName)
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
    function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User)
        Assert-Recovery ($AtLogOn -and $User -ceq $script:currentSid) 'the task starts at this user''s sign-in'
        @{ User = $User }
    }
    function New-ScheduledTaskSettingsSet {
        param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, $ExecutionTimeLimit, $RestartCount, $RestartInterval)
        Assert-Recovery ($RestartCount -eq 3 -and $RestartInterval.TotalSeconds -eq 60 -and $ExecutionTimeLimit -eq [TimeSpan]::Zero) 'startup retry is bounded to three attempts at one-minute intervals with no time limit'
        return @{ RestartCount = $RestartCount }
    }
    function New-ODSInteractiveScheduledTaskPrincipal { param($RunLevel)
        Assert-Recovery ($RunLevel -eq 'Limited') 'the runtime task runs with a limited token'
        @{ UserId = $script:currentSid }
    }
    function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
        $script:events.Add('register:' + $TaskName)
        $script:lastDescription = $Description
        $script:tasks[$TaskName] = [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; Principal = $Principal; Actions = @($Action); State = 'Ready' }
    }
    $exeDir = Join-Path (Join-Path (Join-Path $fixture 'ODS') 'llama.cpp') 'b9014-win-vulkan-x64'
    $plan = [ordered]@{ ExecutablePath = (Join-Path $exeDir 'llama-server.exe'); Port = 18080; ModelsDir = (Get-ODSPortalModelsDir); ContextSize = 65536; GgufFile = 'Model.gguf' }
    $options = [ordered]@{ schemaVersion = 1; Device = 'Vulkan0'; NGpuLayers = 'auto'
        ApiKeyPath = (Join-Path (Get-ODSPortalRuntimeDir) 'api-key'); LogPath = (Join-Path (Get-ODSPortalRuntimeDir) 'llama-server.log')
        ReleaseTag = 'b9014'; ZipSha256 = ('a' * 64); ReasoningArguments = @('--reasoning', 'off'); ExtraArguments = @() }
    $registration = New-ODSPortalRuntimeAction $plan $options (New-ODSNativeLlamaApiKey) 'Ubuntu-24.04' '/home/user/ods'
    $taskName = Register-ODSPortalRuntimeTask $registration
    Assert-Recovery ($taskName -ceq $firstName -and $script:tasks.ContainsKey($firstName) -and
        $script:lastDescription -match 'llama\.cpp' -and $script:lastDescription -match '127\.0\.0\.1') 'registration creates only this user''s llama.cpp task'
    Assert-Recovery (-not ($script:events | Where-Object { $_ -like 'register:ODSLemonade*' })) 'no Lemonade-era task is ever registered'
    $found = Get-ODSPortalRuntimeTask
    Assert-Recovery ($found.TaskName -ceq $firstName) 'the registered task is found by its exact per-user name'
    $script:tasks[$firstName].Principal = @{ UserId = 'S-1-5-21-100-200-300-1002' }
    $message = ''
    try { $null = Get-ODSPortalRuntimeTask } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'different Windows user') 'a task under our name owned by another user fails closed'
    $script:tasks[$firstName].Principal = @{ UserId = $script:currentSid }
    $script:unreadableTask = $firstName
    $message = ''
    try { $null = Get-ODSPortalRuntimeTask } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'Cannot inspect') 'an unreadable task bearing our SID fails closed'
    $script:unreadableTask = ''

    # Retire-only recognition of the former Lemonade tasks.
    $lemonadeExe = Join-Path $fixture 'lemonade_server\bin\LemonadeServer.exe'
    $direct = [pscustomobject]@{ Execute = $lemonadeExe; WorkingDirectory = (Split-Path -Parent $lemonadeExe)
        Arguments = 'serve --port 13305 --host 127.0.0.1 --no-tray --llamacpp vulkan --extra-models-dir "' + (Get-ODSPortalModelsDir) + '"' }
    $legacy = [pscustomobject]@{ TaskName = 'ODSLemonadeRuntime'; TaskPath = '\'; Principal = @{ UserId = $script:currentSid }; Actions = @($direct); State = 'Ready' }
    $current = [pscustomobject]@{ TaskName = (Get-ODSPortalLemonadeTaskName); TaskPath = '\'; Principal = @{ UserId = $script:currentSid }; Actions = @($direct); State = 'Ready' }
    $foreign = [pscustomobject]@{ TaskName = 'ForeignLemonadeRuntime'; TaskPath = '\'; Principal = @{ UserId = $script:currentSid }; Actions = @($direct); State = 'Running' }
    $script:tasks.Clear(); $script:queried.Clear()
    $script:tasks['ODSLemonadeRuntime'] = $legacy
    $script:tasks[$current.TaskName] = $current
    $script:tasks['ForeignLemonadeRuntime'] = $foreign
    $launches = @(Get-ODSPortalLemonadeLaunches)
    Assert-Recovery ($launches.Count -eq 2 -and $launches[0].Launch.TaskName -ceq $current.TaskName -and $launches[1].Launch.TaskName -ceq 'ODSLemonadeRuntime') 'both ODS Lemonade names of this user are recognized, per-user name first'
    Assert-Recovery ($launches[0].Launch.ExecutablePath -ceq $lemonadeExe -and $launches[0].Launch.Port -eq 13305) 'the former executable and port come from the ODS task action'
    Assert-Recovery (-not $script:queried.Contains('ForeignLemonadeRuntime')) 'a Lemonade task ODS never owned (ForeignLemonadeRuntime) is never examined'
    $legacy.Principal = @{ UserId = 'S-1-5-21-100-200-300-1002' }
    $launches = @(Get-ODSPortalLemonadeLaunches)
    Assert-Recovery ($launches.Count -eq 1 -and $launches[0].Launch.TaskName -ceq $current.TaskName) 'another account''s legacy task is preserved and never adopted'
    $legacy.Principal = @{ UserId = $script:currentSid }
    $script:unreadableTask = 'ODSLemonadeRuntime'
    $launches = @(Get-ODSPortalLemonadeLaunches)
    Assert-Recovery ($launches.Count -eq 1) 'an unreadable legacy task cannot block this user and is never changed'
    $script:unreadableTask = $current.TaskName
    $message = ''
    try { $null = Get-ODSPortalLemonadeLaunches } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'Cannot inspect') 'an unreadable Lemonade task bearing our SID fails closed'
    $script:unreadableTask = ''
    $script:tasks.Remove('ODSLemonadeRuntime')
    $current.Actions = @([pscustomobject]@{ Execute = 'unrelated.exe'; Arguments = ''; WorkingDirectory = $fixture })
    $message = ''
    try { $null = Get-ODSPortalLemonadeLaunches } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'not recognized') 'same SID alone cannot authorize retiring a foreign action'
    $current.Actions = @([pscustomobject]@{ Execute = (Join-Path $fixture 'lemonade_server\bin\lemonade-router.exe'); WorkingDirectory = (Join-Path $fixture 'lemonade_server\bin')
        Arguments = $direct.Arguments })
    $message = ''
    try { $null = Get-ODSPortalLemonadeLaunches } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'not recognized') 'a look-alike Lemonade binary name is not an ODS launcher'

    $intentPath = Join-Path (Get-ODSPortalRuntimeDir) 'intent.json'
    Set-ODSPortalRuntimeIntent 'stopped'
    Assert-Recovery (-not (Test-ODSNativeLlamaWanted $intentPath)) 'a durable deliberate stop prevents automatic restart'
    Set-ODSPortalRuntimeIntent 'running'
    Assert-Recovery (Test-ODSNativeLlamaWanted $intentPath) 'an explicit start re-arms the durable launch intent'
    [IO.File]::WriteAllText($intentPath, '{"State":"broken"}')
    $message = ''
    try { $null = Test-ODSNativeLlamaWanted $intentPath } catch { $message = $_.Exception.Message }
    Assert-Recovery ($message -match 'intent is invalid') 'invalid intent fails closed'
} finally {
    $env:LOCALAPPDATA = $previousLocal
    if (-not ([IO.Path]::GetFullPath($fixture)).StartsWith(([IO.Path]::GetFullPath([IO.Path]::GetTempPath())), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Fixture cleanup escaped its temporary directory.'
    }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows Portal AMD recovery contracts."
