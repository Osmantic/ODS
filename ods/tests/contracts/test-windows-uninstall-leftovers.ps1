# Behavioural contract for ods.ps1 uninstall with resources compose down
# does not know about (older releases, disabled extensions). The real
# functions are loaded from ods.ps1's AST; Docker and host helpers are stubs.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../installers/windows/ods.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "ods.ps1 does not parse: $($errors[0].Message)" }
foreach ($name in @('Invoke-Uninstall', 'Remove-ODSDockerProjectByLabel', 'Get-ODSDockerProjectResourceNames', 'Test-ODSArgumentPresent')) {
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
function Get-ScheduledTask { param($TaskName, $ErrorAction) if ($script:tasks.ContainsKey($TaskName)) { return [pscustomobject]@{ TaskName = $TaskName; State = $script:tasks[$TaskName] } } }
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
    switch -Regex ($line) {
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
        '^network rm ' { foreach ($n in @($args[2..($args.Count - 1)])) { $script:networks.Remove($n) | Out-Null }; return }
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
}

$script:InstallDir = Join-Path ([IO.Path]::GetTempPath()) 'ods-uninstall-contract'
$InstallDir = $script:InstallDir
$null = New-Item -ItemType Directory -Path $InstallDir -Force
try {
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
} finally {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Passed $script:checks Windows uninstall leftover contracts."
