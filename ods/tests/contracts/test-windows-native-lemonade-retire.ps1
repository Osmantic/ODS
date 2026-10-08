[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'Fixture mocks keep the production command signatures.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidOverwritingBuiltInCmdlets', '', Justification = 'Task Scheduler, process and socket cmdlets are replaced by fixtures.')]
[CmdletBinding()]
param()
# Retire-only migration from the legacy native installer's Lemonade runtime
# (Round F). ODS recognizes ODSLemonadeRuntime only when its action is a
# launcher ODS wrote for this installation, stops only the process tree it
# proves, and unregisters the task only after llama-server proved its model.
# Lemonade's folders, cache, registry and MSI are never touched, and the
# separate ForeignLemonadeRuntime task is never even looked up. Task
# Scheduler, processes and sockets are fixtures; only temporary files are
# written. Replaces test-windows-lemonade-task-cleanup.ps1, whose kill-by-
# folder cleanup Round F deleted.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
. (Join-Path $root 'installers/windows/lib/native-lemonade-retire.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}

$script:output = @()
function Write-AI { param([string]$Message) $script:output += $Message }
function Write-AIWarn { param([string]$Message) $script:output += ('WARN ' + $Message) }
# Nothing here may reach the real system.
function msiexec { throw 'msiexec must never run' }
function Start-Process { throw 'Start-Process must never run during retirement' }
function Stop-Process { throw 'retirement stops processes only through held handles' }

$script:queried = @()
$script:task = $null
$script:unreadable = $false
function Get-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, [string]$TaskPath)
    $script:queried += ,@($TaskName, $TaskPath)
    if ($script:unreadable) { Write-Error -Message 'Access is denied.' -Category PermissionDenied; return }
    if ($script:task -and $TaskName -eq 'ODSLemonadeRuntime') { return $script:task }
}
$script:calls = @()
function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:calls += "disable:$TaskName"; return $null }
function Stop-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:calls += "stop:$TaskName" }
function Unregister-ScheduledTask { param($TaskName, $TaskPath, [switch]$Confirm, $ErrorAction) $script:calls += "unregister:$TaskName" }
$script:engine = 0
function Get-ODSLegacyTaskEngineId { param([string]$TaskName) $script:calls += "engine:$TaskName"; return $script:engine }
$script:nodes = @()
function Get-CimInstance { param($ClassName, $ErrorAction) return $script:nodes }
$script:listeners = @()
function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) return @($script:listeners | Where-Object { $_.LocalPort -eq $LocalPort }) }
$script:treeRoots = @()
function Get-ODSPortalOwnedProcessTree([object[]]$Roots, [object[]]$Nodes) {
    $script:treeRoots += ,@($Roots | ForEach-Object { $_.ProcessId })
    $handle = [pscustomobject]@{ Id = $Roots[0].ProcessId }
    $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
    return [pscustomobject]@{ Nodes = $Roots; Handles = @($handle) }
}
$script:stoppedHandles = @()
function Stop-ODSPortalOwnedProcesses($Handles) { $script:stoppedHandles += @($Handles | ForEach-Object { $_.Id }) }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-lemonade-retire-' + [guid]::NewGuid().ToString('N'))
$install = Join-Path $fixture 'install dir'
$models = Join-Path (Join-Path $install 'data') 'models'
$logs = Join-Path $install 'logs'
$null = New-Item -ItemType Directory -Path $models, $logs -Force
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$lemonadeExe = 'C:\Users\fixture\AppData\Local\lemonade_server\bin\lemonade-server.exe'
$serveArgs = 'serve --port 8080 --host 127.0.0.1 --no-tray --llamacpp vulkan --extra-models-dir "' + $models + '" --ctx-size 65536'
function New-Task($Actions, [string]$UserId = $sid) {
    return [pscustomobject]@{ TaskName = 'ODSLemonadeRuntime'; TaskPath = '\'; State = 'Ready'
        Principal = [pscustomobject]@{ UserId = $UserId }; Actions = @($Actions) }
}
function New-Action([string]$Execute, [string]$Arguments, [string]$WorkingDirectory) {
    return [pscustomobject]@{ Execute = $Execute; Arguments = $Arguments; WorkingDirectory = $WorkingDirectory }
}
try {
    # --- Recognition: only launchers ODS wrote for this installation ---
    Check ($null -eq (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080)) 'no ODSLemonadeRuntime task means nothing to retire'

    $direct = New-Action $lemonadeExe $serveArgs (Split-Path -Parent $lemonadeExe)
    $script:task = New-Task $direct
    $owned = Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080
    Check ($owned.Owned -and $owned.Generation -eq 'direct' -and $owned.ExecutablePath -eq $lemonadeExe -and
        @($owned.CommandLines) -contains ('"' + $lemonadeExe + '" ' + $serveArgs)) 'the direct "serve" launch ODS wrote for this port and models folder is recognized with its exact command line'

    foreach ($case in @(
        @{ Name = 'another port'; Action = (New-Action $lemonadeExe ($serveArgs -replace '--port 8080', '--port 13305') (Split-Path -Parent $lemonadeExe)) },
        @{ Name = 'another models folder'; Action = (New-Action $lemonadeExe ($serveArgs -replace [regex]::Escape($models), 'D:\models') (Split-Path -Parent $lemonadeExe)) },
        @{ Name = 'a LAN listener'; Action = (New-Action $lemonadeExe ($serveArgs -replace '127\.0\.0\.1', '0.0.0.0') (Split-Path -Parent $lemonadeExe)) },
        @{ Name = 'another program'; Action = (New-Action 'C:\Tools\llama-server.exe' $serveArgs 'C:\Tools') },
        @{ Name = 'a relative executable'; Action = (New-Action 'lemonade-server.exe' $serveArgs 'C:\Tools') })) {
        $script:task = New-Task $case.Action
        Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) "a Lemonade task with $($case.Name) is not ODS's"
    }
    $script:task = New-Task @($direct, $direct)
    Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) 'a task with extra actions is not ODS''s'
    $script:task = New-Task $direct 'S-1-5-21-1000-1000-1000-1001'
    $other = Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080
    Check (-not $other.Owned -and $other.Reason -match 'another Windows user') 'another Windows user''s task is never adopted'
    $script:unreadable = $true
    $unreadable = Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080
    Check (-not $unreadable.Owned -and $unreadable.Reason -match 'cannot be read') 'an unreadable task is not treated as absent or owned'
    $script:unreadable = $false

    # The 10.7-era wrapper: powershell -File <install>\logs\lemonade-launch.task.ps1
    $shell = @(Get-Command powershell.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($shell.Count) {
        $wrapperPath = Join-Path $logs 'lemonade-launch.task.ps1'
        $wrapperArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $wrapperPath + '"'
        function Write-Wrapper([string]$Exe, [string]$Arguments, [string]$Extra = '') {
            $text = "`$ErrorActionPreference = 'Stop'`r`n`$exe = '$Exe'`r`n`$argumentString = '$Arguments'`r`n" +
                "`$workingDirectory = '$(Split-Path -Parent $Exe)'`r`n`$envPath = 'C:\fixture\.env'`r`n$Extra" +
                "try {`r`n    `$child = Start-Process -FilePath `$exe -ArgumentList `$argumentString -WorkingDirectory `$workingDirectory -WindowStyle Hidden -PassThru`r`n" +
                "    `$child.WaitForExit()`r`n    exit `$child.ExitCode`r`n} catch { exit 1 }`r`n"
            [IO.File]::WriteAllText($wrapperPath, $text, [Text.UTF8Encoding]::new($false))
        }
        $wrapperAction = New-Action $shell[0].Source $wrapperArgs (Split-Path -Parent $lemonadeExe)
        $script:task = New-Task $wrapperAction
        Write-Wrapper $lemonadeExe '--port 8080 --host 127.0.0.1'
        $wrapped = Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080
        Check ($wrapped.Owned -and $wrapped.Generation -eq 'wrapper' -and $wrapped.ExecutablePath -eq $lemonadeExe -and
            $wrapped.WrapperPath -eq $wrapperPath) 'the 10.7 launcher script ODS wrote is recognized without running it'
        Write-Wrapper $lemonadeExe '--port 9000 --host 127.0.0.1'
        Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) 'a launcher script for another port is not ODS''s'
        Write-Wrapper 'C:\Tools\other.exe' '--port 8080 --host 127.0.0.1'
        Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) 'a launcher script that starts another program is not ODS''s'
        Write-Wrapper $lemonadeExe '--port 8080 --host 127.0.0.1' "`$exe = 'C:\Tools\lemonade-server.exe'`r`n"
        Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) 'a launcher script that reassigns its executable is not ODS''s'
        $script:task = New-Task (New-Action $shell[0].Source ($wrapperArgs + ' -Command calc') (Split-Path -Parent $lemonadeExe))
        Write-Wrapper $lemonadeExe '--port 8080 --host 127.0.0.1'
        Check (-not (Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080).Owned) 'a task that adds commands to the launcher is not ODS''s'
    }

    # --- Stop: only the proven tree; a user's own Lemonade keeps running ---
    $script:task = New-Task $direct
    $runtime = Get-ODSLegacyLemonadeRuntime -InstallDir $install -Port 8080
    $commandLine = '"' + $lemonadeExe + '" ' + $serveArgs
    $userLemonade = [pscustomobject]@{ ProcessId = 900; ParentProcessId = 1; Name = 'LemonadeServer.exe'
        ExecutablePath = 'C:\Program Files\Lemonade Server\bin\LemonadeServer.exe'; CommandLine = '"C:\Program Files\Lemonade Server\bin\LemonadeServer.exe"' }
    $script:nodes = @(
        [pscustomobject]@{ ProcessId = 500; ParentProcessId = 4; Name = 'lemonade-server.exe'; ExecutablePath = $lemonadeExe; CommandLine = $commandLine },
        [pscustomobject]@{ ProcessId = 501; ParentProcessId = 500; Name = 'lemonade-router.exe'; ExecutablePath = 'C:\Users\fixture\AppData\Local\lemonade_server\bin\lemonade-router.exe'; CommandLine = 'router' },
        $userLemonade,
        [pscustomobject]@{ ProcessId = 901; ParentProcessId = 1; Name = 'lemonade-server.exe'; ExecutablePath = $lemonadeExe; CommandLine = ('"' + $lemonadeExe + '" serve') })
    $script:engine = 500
    $script:calls = @(); $script:treeRoots = @(); $script:stoppedHandles = @(); $script:listeners = @()
    $result = Stop-ODSLegacyLemonadeRuntime -Runtime $runtime -PidFile (Join-Path $install 'data\llama-server.pid') -Port 8080
    Check ($script:calls[0] -eq 'disable:ODSLemonadeRuntime' -and $script:calls -contains 'stop:ODSLemonadeRuntime') 'the ODS task is disabled before its running instance is stopped'
    Check ($script:treeRoots.Count -eq 1 -and (@($script:treeRoots[0]) -join ',') -eq '500' -and (@($script:stoppedHandles) -join ',') -eq '500') 'only the running task instance (exact executable and command line) roots the stopped tree'
    Check ($result.Stopped -eq 1 -and -not $result.Left) 'a freed port reports nothing left behind'

    $script:nodes[0].CommandLine = '"' + $lemonadeExe + '" serve --port 8080'
    $script:calls = @(); $script:treeRoots = @(); $script:stoppedHandles = @()
    Check ((Get-Failure { $null = Stop-ODSLegacyLemonadeRuntime -Runtime $runtime -PidFile (Join-Path $install 'data\llama-server.pid') -Port 8080 }) -match 'Cannot prove') 'a running instance whose command line differs is never stopped'
    Check ($script:stoppedHandles.Count -eq 0) 'nothing was stopped when ownership could not be proven'
    $script:nodes[0].CommandLine = $commandLine

    # Without a running instance only the PID-file process with the task's own
    # executable and command line qualifies.
    $script:engine = 0
    $pidFile = Join-Path (Join-Path $install 'data') 'llama-server.pid'
    Set-Content -LiteralPath $pidFile -Value '901'
    $script:calls = @(); $script:treeRoots = @(); $script:stoppedHandles = @()
    $result = Stop-ODSLegacyLemonadeRuntime -Runtime $runtime -PidFile $pidFile -Port 8080
    Check ($script:stoppedHandles.Count -eq 0 -and $result.Stopped -eq 0) 'a PID-file process with another command line is not ODS''s, even with the same executable'
    Set-Content -LiteralPath $pidFile -Value '500'
    $result = Stop-ODSLegacyLemonadeRuntime -Runtime $runtime -PidFile $pidFile -Port 8080
    Check ((@($script:stoppedHandles) -join ',') -eq '500' -and -not ($script:calls -contains 'stop:ODSLemonadeRuntime')) 'the PID-file process with the exact task command line is stopped through its handle'
    $script:listeners = @([pscustomobject]@{ LocalPort = 8080; OwningProcess = 900 })
    $script:stoppedHandles = @()
    Set-Content -LiteralPath $pidFile -Value '900'
    $result = Stop-ODSLegacyLemonadeRuntime -Runtime $runtime -PidFile $pidFile -Port 8080
    Check ($script:stoppedHandles.Count -eq 0 -and $result.Left -match 'LemonadeServer\.exe' -and $result.Left -match 'left it running') 'a user''s Lemonade on the port is reported and left running'
    $script:listeners = @()

    # --- Complete: unregister the unchanged, idle task once; notice once ---
    $msiLog = Join-Path $logs 'lemonade-msi-install.log'
    [IO.File]::WriteAllText($msiLog, "Property(S): ProductVersion = 10.0.0`r`n")
    $script:calls = @(); $script:output = @()
    Complete-ODSLegacyLemonadeRetirement -Runtime $runtime -InstallDir $install
    Check ($script:calls -contains 'unregister:ODSLemonadeRuntime') 'the unchanged idle ODS task is unregistered after llama-server proved its model'
    Check ((@($script:output) -join ' ') -match 'ODS installed Lemonade Server 10\.0\.0 on .* no longer uses it.*Settings > Apps.*left it unchanged') 'the one-time notice names the version ODS installed and leaves uninstalling to the user'
    Check (Test-Path -LiteralPath (Join-Path (Join-Path $install 'data') 'lemonade-retired.json')) 'the notice is recorded'
    $script:output = @()
    Complete-ODSLegacyLemonadeRetirement -Runtime $runtime -InstallDir $install
    Check ($script:output.Count -eq 0) 'the notice is shown once'
    Remove-Item -LiteralPath (Join-Path (Join-Path $install 'data') 'lemonade-retired.json')
    Remove-Item -LiteralPath $msiLog
    $script:output = @()
    Complete-ODSLegacyLemonadeRetirement -Runtime $runtime -InstallDir $install
    Check ((@($script:output) -join ' ') -match 'Lemonade installation was left unchanged') 'without ODS''s MSI log the notice only says ODS stopped using Lemonade'

    $script:task = New-Task (New-Action $lemonadeExe ($serveArgs + ' --extra') (Split-Path -Parent $lemonadeExe))
    $script:calls = @(); $script:output = @()
    Complete-ODSLegacyLemonadeRetirement -Runtime $runtime -InstallDir $install
    Check (-not ($script:calls -contains 'unregister:ODSLemonadeRuntime') -and (@($script:output) -join ' ') -match 'left it registered') 'a task changed after it was stopped is left registered (disabled)'
    $script:task = New-Task $direct
    $script:engine = 77
    $script:calls = @()
    Complete-ODSLegacyLemonadeRetirement -Runtime $runtime -InstallDir $install
    Check (-not ($script:calls -contains 'unregister:ODSLemonadeRuntime')) 'a task that runs again is not unregistered'

    # --- Only ODSLemonadeRuntime at the root folder was ever looked up ---
    $names = @($script:queried | ForEach-Object { $_[0] } | Sort-Object -Unique)
    Check (($names -join ',') -eq 'ODSLemonadeRuntime' -and @($script:queried | Where-Object { $_[1] -ne '\' }).Count -eq 0) 'no other task (ForeignLemonadeRuntime included) is looked up, and nothing is enumerated'
    $tokens = $null; $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/lib/native-lemonade-retire.ps1'), [ref]$tokens, [ref]$parseErrors)
    $code = (@($tokens | Where-Object { $_.Kind -ne 'Comment' } | ForEach-Object { $_.Text }) -join ' ')
    Check ($parseErrors.Count -eq 0 -and $code -notmatch '(?i)msiexec|Stop-Process|lemonade_server|\.cache|Program Files') 'the retire code never runs msiexec, kills by name, or touches Lemonade''s folders'
} finally {
    Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "PASS ($script:checks checks)"
