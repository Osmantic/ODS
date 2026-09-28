# Behavioural contract for ods.ps1 uninstall with resources compose down
# does not know about (older releases, disabled extensions). The real
# functions are loaded from ods.ps1's AST; Docker and host helpers are stubs.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../installers/windows/ods.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "ods.ps1 does not parse: $($errors[0].Message)" }
foreach ($name in @('Invoke-Uninstall', 'Remove-ODSDockerProjectByLabel', 'Get-ODSDockerProjectResourceNames', 'Test-ODSArgumentPresent', 'Assert-ODSDockerProjectOwnership', 'Test-ODSUninstallPathOwned', 'Resolve-ODSUninstallLiteral', 'Test-ODSUninstallCommandOwned', 'Test-ODSUninstallTaskOwned', 'Stop-ODSUninstallOwnedHelpers')) {
    $definition = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $definition) { throw "ods.ps1 no longer defines $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}

$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}

# Host-side stubs: nothing here touches the machine.
function Write-AI { param([string]$m) $script:output.Add($m) }
function Write-AIWarn { param([string]$m) $script:output.Add($m) }
function Write-AIError { param([string]$m) $script:output.Add($m) }
function Write-AISuccess { param([string]$m) $script:output.Add($m) }
function Test-ODSDockerRunningQuiet { return $true }
function Assert-ODSInstallDirSafeForRemoval { }
function Invoke-Agent { param([string]$Action) }
function Stop-ODSOpenCodeRuntime { }
function Get-NativeInferenceBackend { return 'none' }
function Stop-NativeInferenceServer { }
$script:ODS_AGENT_TASK_NAME = 'ODSHostAgent'; $script:ODS_MODEL_UPGRADE_TASK_NAME = 'ODSModelUpgrade'
$script:LEMONADE_TASK_NAME = 'ODSLemonadeRuntime'; $script:OPENCODE_TASK_NAME = 'ODSOpenCodeWeb'; $script:NATIVE_LLAMA_TASK_NAME = 'ODSNativeLlamaRuntime'
function Get-ScheduledTask { param($TaskName, $ErrorAction) if ($script:tasks.ContainsKey($TaskName)) { return [pscustomobject]@{ TaskName = $TaskName; State = $script:tasks[$TaskName]; Actions = @([pscustomobject]@{Execute='python.exe'; Arguments=('"{0}/scripts/ods-host-agent.py"' -f $script:taskRoot); WorkingDirectory=$script:taskRoot}) } } }
function Get-CimInstance { param($ClassName, $ErrorAction) return $script:processes }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:stoppedProcesses.Add([int]$Id) }
function Stop-ScheduledTask { param($TaskName, $ErrorAction) $script:tasks[$TaskName] = 'Ready' }
function Unregister-ScheduledTask {
    param($TaskName, $Confirm, $ErrorAction)
    if ($TaskName -in $script:lockedTasks) { throw [Microsoft.Management.Infrastructure.CimException]::new('Access is denied.') }
    $script:tasks.Remove($TaskName)
}
function Get-ComposeFlags { return @('-f', 'docker-compose.base.yml') }
function Test-ODSComposeFlagsFilesAvailable { param([string[]]$ComposeFlags) return $true }
function Remove-ODSInstallDirectory { param([switch]$KeepData, [switch]$KeepModels) $script:dirRemoved = $true }

# Minimal Docker model: compose down removes only what the compose files know.
function docker {
    $line = $args -join ' '
    $script:dockerCalls.Add($line)
    $global:LASTEXITCODE = 0
    if ($script:listFailure -and $line -match '^ps -a ') { $global:LASTEXITCODE = 1; return }
    switch -Regex ($line) {
        '^container inspect ' { return ConvertTo-Json -Depth 6 -InputObject @(@{ Id=$args[2]; Config = @{ Labels = @{ 'com.docker.compose.project' = 'ods'; 'com.docker.compose.project.working_dir' = $script:containerRoot } }; Mounts = @($script:volumes | Where-Object { $_ -ne $script:unattachedVolume } | ForEach-Object { @{ Type = 'volume'; Name = $_ } }) + @(@{Type='volume';Name='foreign-external-data'}); NetworkSettings = @{Networks = @{ 'ods-network' = @{ NetworkID = 'verified-network-id' }; 'foreign-external-network' = @{ NetworkID='foreign-external-network-id' } }} }) }
        '^network inspect ' { if ($script:networkInspectFailure) { $global:LASTEXITCODE=1; return }; return ConvertTo-Json -Depth 4 -InputObject @(@{Id=$(if ($args[2] -eq 'ods-network') {'verified-network-id'} else {'unrelated-network-id'}); Containers=$(if($script:foreignNetworkAttachment){@{'foreign-container-id'=@{}}}else{@{}})}) }
        '^compose .*down' { foreach ($c in @($script:containers)) { if ($c -ne $script:composeDownKeeps) { $script:containers.Remove($c) | Out-Null } }; $script:networks.Remove('ods-network') | Out-Null; foreach ($v in @($script:composeVolumes)) { $script:volumes.Remove($v) | Out-Null }; return }
        '^ps -a --filter \S+ --format \{\{\.Names\}\}$' { return @($script:containers) }
        '^network ls --filter \S+ --format \{\{\.Name\}\}$' { return @($script:networks) }
        '^volume ls -q --filter' { return @($script:volumes) }
        '^rm -f ' {
            foreach ($c in @($args[2..($args.Count - 1)])) {
                if ($c -in $script:busyContainers) { $global:LASTEXITCODE = 1; continue }
                $script:containers.Remove($c) | Out-Null
            }
            return
        }
        '^network rm ' { foreach ($n in @($args[2..($args.Count - 1)])) { $script:networks.Remove($(if ($n -eq 'verified-network-id') {'ods-network'} else {$n})) | Out-Null }; return }
        '^volume rm ' {
            foreach ($v in @($args[2..($args.Count - 1)])) {
                if ($v -in $script:busyVolumes) { $global:LASTEXITCODE = 1; continue }
                $script:volumes.Remove($v) | Out-Null
            }
            return
        }
        default { throw "Unexpected docker call: $line" }
    }
}

function Reset-Docker([string[]]$ExtraVolumes, [string[]]$Busy = @(), [string[]]$BusyContainers = @()) {
    $script:output = [Collections.Generic.List[string]]::new()
    $script:dockerCalls = [Collections.Generic.List[string]]::new()
    $script:containers = [Collections.Generic.List[string]]::new(); $script:containers.Add('ods-dashboard-api')
    $script:networks = [Collections.Generic.List[string]]::new(); $script:networks.Add('ods-network')
    $script:composeVolumes = @('ods_perplexica-data', 'ods_perplexica-uploads')
    $script:volumes = [Collections.Generic.List[string]]::new()
    foreach ($v in @($script:composeVolumes + $ExtraVolumes)) { if ($v) { $script:volumes.Add($v) } }
    $script:busyVolumes = $Busy
    $script:busyContainers = $BusyContainers
    $script:dirRemoved = $false
    $script:tasks = @{ ODSHostAgent = 'Ready'; ODSOpenCodeWeb = 'Running'; ODSNativeLlamaRuntime = 'Ready' }
    $script:lockedTasks = @()
    $script:containerRoot = $InstallDir
    $script:listFailure = $false
    $script:unattachedVolume = ''
    $script:networkInspectFailure = $false
    $script:foreignNetworkAttachment = $false
    $script:taskRoot = $InstallDir
    $script:processes = @()
    $script:stoppedProcesses = [Collections.Generic.List[int]]::new()
}

$script:InstallDir = Join-Path ([IO.Path]::GetTempPath()) 'ods-uninstall-contract'
$InstallDir = $script:InstallDir
$null = New-Item -ItemType Directory -Path $InstallDir -Force
try {
    Reset-Docker @()
    $script:networks.Add('ods-other-wsl-network')
    $message=''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message=$_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OWNERSHIP_UNKNOWN:*' -and $script:tasks.Count -eq 3 -and -not $script:dirRemoved -and -not ($script:dockerCalls -match '^(compose|rm|network rm|volume rm) ')) 'an unattached foreign network is rejected before any cleanup'

    Reset-Docker @()
    $script:networkInspectFailure=$true
    $message=''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message=$_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OWNERSHIP_UNKNOWN:*' -and -not $script:dirRemoved -and $script:tasks.Count -eq 3) 'failed network inspection cannot authorize cleanup'

    Reset-Docker @()
    $script:foreignNetworkAttachment=$true
    $message=''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message=$_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OTHER_INSTALLATION:*' -and $script:tasks.Count -eq 3 -and -not $script:dirRemoved -and -not ($script:dockerCalls -match '^(compose|rm|network rm|volume rm) ')) 'a network shared with a foreign container is rejected before changes'

    Reset-Docker @()
    $script:taskRoot = 'C:\AnotherInstallation\ods'
    $script:processes = @(
        [pscustomobject]@{ProcessId=$PID;ParentProcessId=912300;Name='pwsh.exe';ExecutablePath='C:\PowerShell\pwsh.exe';CommandLine=('pwsh -File "{0}/ods.ps1" uninstall' -f $InstallDir)},
        [pscustomobject]@{ProcessId=912300;ParentProcessId=0;Name='pwsh.exe';ExecutablePath='C:\PowerShell\pwsh.exe';CommandLine=('pwsh -Command "& ''{0}/ods.ps1'' uninstall"' -f $InstallDir)},
        [pscustomobject]@{ProcessId=912301;Name='python.exe';ExecutablePath='C:\Python\python.exe';CommandLine='python.exe C:\AnotherInstallation\ods\scripts\ods-host-agent.py --port 3003'},
        [pscustomobject]@{ProcessId=912302;Name='python.exe';ExecutablePath='C:\Python\python.exe';CommandLine=('python.exe "{0}/scripts/ods-host-agent.py"' -f $InstallDir)},
        [pscustomobject]@{ProcessId=912303;ParentProcessId=912302;Name='lemonade-server.exe';ExecutablePath='C:\Shared\lemonade-server.exe';CommandLine='lemonade-server --port 8080'}
    )
    Invoke-Uninstall -UninstallArgs @('--force')
    Check ($script:tasks.Count -eq 3 -and $script:tasks.ODSOpenCodeWeb -eq 'Running') 'foreign scheduled tasks are neither stopped nor unregistered'
    Check ($script:stoppedProcesses.Count -eq 2 -and $script:stoppedProcesses[0] -eq 912303 -and $script:stoppedProcesses[1] -eq 912302) 'only the owned launcher and its captured runtime child stop, preserving foreign helpers and shell ancestry'
    Check (-not ($script:dockerCalls -match 'foreign-external-(data|network-id)')) 'external attachments never enter the deletion plan'
    Check (-not ($script:dockerCalls -match '^compose .*down')) 'saved compose flags cannot expand the deletion plan to another project'

    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(('Start-Process python.exe -ArgumentList @(''{0}/scripts/ods-host-agent.py'')' -f $InstallDir)))
    Check (Test-ODSUninstallTaskOwned ([pscustomobject]@{Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments="-NoProfile -EncodedCommand $encoded"})})) 'an encoded host-agent launcher is identified without executing it'
    Check (-not (Test-ODSUninstallTaskOwned ([pscustomobject]@{Actions=@([pscustomobject]@{Execute='python.exe';Arguments='C:\Unrelated\agent.py';WorkingDirectory=$InstallDir})}))) 'working directory alone cannot authorize task deletion'
    Check (-not (Test-ODSUninstallCommandOwned ('"{0}-other/agent.py"' -f $InstallDir)) -and -not (Test-ODSUninstallCommandOwned ('"{0}/../other/agent.py"' -f $InstallDir))) 'sibling prefixes and path traversal cannot authorize helper deletion'
    Check (-not (Test-ODSUninstallCommandOwned ('python C:\Foreign\agent.py --log "{0}/logs/foreign.txt"' -f $InstallDir))) 'a data or log argument is insufficient helper ownership'
    Check (-not (Test-ODSUninstallCommandOwned 'powershell.exe -Command "Write-Host ''fixture|invalid-path''"')) 'non-path command literals do not abort ownership inspection'
    Check (-not (Test-ODSUninstallCommandOwned ('powershell.exe -Command "Get-FileHash ''{0}/ods.ps1''"' -f $InstallDir))) 'reading an owned file does not authorize killing an unrelated shell'
    Check (-not (Test-ODSUninstallCommandOwned ('python.exe -c "print(''{0}/agent.py'')"' -f $InstallDir))) 'Python inline data references do not establish script ownership'
    Check (-not (Test-ODSUninstallCommandOwned ('python.exe ''{0}/agent.py''' -f $InstallDir))) 'single quotes do not invent Windows argument grouping'
    $generated = ('$env:PATH=''C:/Docker;''+$env:PATH; $agentArgs=@(''-3'')+@(''{0}/scripts/ods-host-agent.py'',''--port'',''3003''); Set-Location ''{0}''; Start-Process -FilePath ''C:/Python/py.exe'' -ArgumentList $agentArgs -WindowStyle Hidden -Wait' -f $InstallDir)
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($generated))
    Check (Test-ODSUninstallTaskOwned ([pscustomobject]@{Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments="-NoProfile -EncodedCommand $encoded"})})) 'the generated array and py launcher is recognized without evaluation'
    foreach ($snippet in @(
        ('Write-Host ''{0}/agent.py''' -f $InstallDir),
        ('function NeverCalled {{ Start-Process python.exe -ArgumentList @(''{0}/agent.py'') }}' -f $InstallDir),
        ('if ($false) {{ Start-Process python.exe -ArgumentList @(''{0}/agent.py'') }}' -f $InstallDir),
        ('Start-Process python.exe -ArgumentList @(''{0}/agent.py''); Start-Process python.exe -ArgumentList @(''C:/Foreign/agent.py'')' -f $InstallDir),
        ('$args=''{0}''+''-foreign/agent.py''; Start-Process python.exe -ArgumentList $args' -f $InstallDir),
        ('$unused=(Set-Alias Start-Process Get-FileHash); Start-Process python.exe -ArgumentList @(''{0}/agent.py'')' -f $InstallDir)
    )) {
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($snippet))
        Check (-not (Test-ODSUninstallTaskOwned ([pscustomobject]@{Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments="-EncodedCommand $encoded"})}))) 'encoded data, deferred, mixed and ambiguous launchers remain untouched'
    }

    Reset-Docker @('ods_old-wsl-data')
    $script:unattachedVolume = 'ods_old-wsl-data'
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OWNERSHIP_UNKNOWN:*' -and $script:tasks.Count -eq 3 -and -not $script:dirRemoved) 'a native container does not authorize deleting unrelated orphan WSL volumes'

    Reset-Docker @()
    $script:listFailure = $true
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -like 'Docker ownership query failed*' -and -not $script:dirRemoved -and $script:tasks.Count -eq 3) 'Docker query failure is not treated as an empty safe project'

    Reset-Docker @('ods_important-data')
    $script:containerRoot = '/home/another-user/ods'
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OTHER_INSTALLATION:*') 'native uninstall rejects a WSL container using the same project name'
    Check (-not $script:dirRemoved -and $script:tasks.Count -eq 3 -and -not ($script:dockerCalls -match '^(compose|rm|volume rm|network rm) ')) 'ownership rejection preserves files, tasks and all Docker data'

    Reset-Docker @('ods_important-data')
    $script:containerRoot = ''
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OWNERSHIP_UNKNOWN:*' -and -not $script:dirRemoved) 'missing origin labels never authorize deletion'

    Reset-Docker @('ods_important-data')
    $script:containers.Clear()
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -like 'ODS_UNINSTALL_OWNERSHIP_UNKNOWN:*' -and -not ($script:dockerCalls -match '^(compose|rm|volume rm|network rm) ')) 'orphan project labels alone cannot prove installation ownership'

    Reset-Docker @()
    Invoke-Uninstall -UninstallArgs @('--force')
    Check $script:dirRemoved 'clean compose down removes the runtime'
    Check ($script:tasks.Count -eq 0) 'uninstall removes the helper scheduled tasks, including the native llama runtime'

    Reset-Docker @()
    $script:lockedTasks = @('ODSOpenCodeWeb')
    Invoke-Uninstall -UninstallArgs @('--force')
    Check (@($script:output | Where-Object { $_ -match "Scheduled task ODSOpenCodeWeb could not be removed .*Unregister-ScheduledTask -TaskName 'ODSOpenCodeWeb'" }).Count -eq 1) 'a task that cannot be removed is reported with the command to remove it'

    Reset-Docker @('ods_open-webui-data')
    Invoke-Uninstall -UninstallArgs @('--force')
    Check (-not ($script:volumes -contains 'ods_open-webui-data')) 'labelled volume unknown to compose is removed by label'
    Check $script:dirRemoved 'leftover labelled volume no longer blocks uninstall'

    Reset-Docker @('ods_open-webui-data') @('ods_open-webui-data')
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -eq 'ODS_UNINSTALL_DOCKER_CLEANUP_INCOMPLETE' -and -not $script:dirRemoved) 'volume that cannot be removed keeps the runtime for recovery'
    Check ($script:output -contains '  still present: ods_open-webui-data') 'incomplete cleanup names the remaining resource'

    # Names, not IDs: a container compose down could not remove is listed by name.
    Reset-Docker @() @() @('ods-legacy-worker')
    $script:containers.Add('ods-legacy-worker')
    $script:composeDownKeeps = 'ods-legacy-worker'
    $message = ''
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $message = $_.Exception.Message }
    Check ($message -eq 'ODS_UNINSTALL_DOCKER_CLEANUP_INCOMPLETE' -and $script:output -contains '  still present: ods-legacy-worker') 'a container that cannot be removed is named in the message'
    $script:composeDownKeeps = $null

    Reset-Docker @('ods_open-webui-data')
    Invoke-Uninstall -UninstallArgs @('--force', '--keep-data')
    Check (($script:volumes -contains 'ods_open-webui-data') -and -not ($script:dockerCalls -match '^volume rm')) '--keep-data never removes volumes'
    Check $script:dirRemoved '--keep-data still completes'

    $quietDefinition = $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-ODSDockerRunningQuiet'}, $true)
    . ([scriptblock]::Create($quietDefinition.Extent.Text))
    function docker { Write-Error 'Successful daemon warning fixture'; $global:LASTEXITCODE = $script:daemonExitCode }
    $script:daemonExitCode=0
    Check ((Test-ODSDockerRunningQuiet) -and $ErrorActionPreference -eq 'Stop') 'a successful daemon warning is accepted and the caller error preference restored'
    $script:daemonExitCode=1
    Check (-not (Test-ODSDockerRunningQuiet)) 'a failed daemon exit remains unavailable despite stderr handling'
    $global:LASTEXITCODE=0
} finally {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Passed $script:checks Windows uninstall leftover contracts."
