# The native RTX Spark route of the Windows Portal setup (PR B4): the stage-1
# order, routing before the NVIDIA container gate, the shared restart and its
# notices, the runtime check and its failure classes (with the Visual C++
# Arm64 offer), the qualification cache and the deferred check, the migration
# of an older container install, replacing a disabled model task ODS does not
# manage, the multi-part model download, and the Linux arguments.
# The RTX Spark is the real probe (tests/fixtures/rtx-spark). WSL, Docker,
# nvidia-smi, scheduled tasks, UAC, winget, downloads and prompts are fixtures;
# only temporary files are written.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-portal-setup.ps1')
$fixtures = Join-Path (Join-Path (Join-Path $root 'tests') 'fixtures') 'rtx-spark'
$llamaFixtures = Join-Path $fixtures 'llama-cpp-b11429'
$installerRoot = Join-Path $root 'installers'
$script:checks = 0
$script:output = [Collections.Generic.List[string]]::new()
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        # What the failing case asked and printed, for the CI log.
        if ($script:prompts) { Microsoft.PowerShell.Utility\Write-Host ('PROMPTS: ' + ($script:prompts -join ' || ')) }
        Microsoft.PowerShell.Utility\Write-Host ('OUTPUT: ' + ($script:output -join ' // '))
        throw $Message
    }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Write-Host {
    param([Parameter(Position = 0, ValueFromRemainingArguments = $true)][object[]]$Object, [object]$ForegroundColor, [switch]$NoNewline)
    $script:output.Add([string](@($Object) -join ' '))
}
function Get-Output { return ($script:output -join "`n") }
function Get-Index([string]$Pattern) {
    for ($index = 0; $index -lt $script:calls.Count; $index++) { if ($script:calls[$index] -like $Pattern) { return $index } }
    return -1
}
$helpLine = 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-spark-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previous = @{ LOCALAPPDATA = $env:LOCALAPPDATA; USERPROFILE = $env:USERPROFILE; AMD_INFERENCE_PORT = $env:AMD_INFERENCE_PORT; SystemDrive = $env:SystemDrive }
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
$env:USERPROFILE = Join-Path $fixture 'Profile'
$env:SystemDrive = 'C:'
Remove-Item Env:\AMD_INFERENCE_PORT -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:USERPROFILE -Force
try {
    # --- The RTX Spark as measured ---------------------------------------------------
    $sparkRows = [IO.File]::ReadAllLines((Join-Path $fixtures 'nvidia-smi-windows.csv'))
    function Test-ODSSparkWindowsArm64 { return $true }
    function Get-ODSSparkNvidiaRows { return @(ConvertFrom-ODSNvidiaSmiCsv $sparkRows) }
    $measuredHost = Get-ODSSparkHost
    Check ($measuredHost.GpuName -ceq 'NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU)' -and $measuredHost.CarveOutMiB -eq 98240 -and
        $measuredHost.ComputeCap -ceq '12.1' -and $measuredHost.DriverVersion -ceq '616.62' -and $measuredHost.PciDeviceId -ceq '0x2e03') 'the fixture host is the RTX Spark as nvidia-smi listed it (N1X, 98,240 MiB, compute 12.1, driver 616.62)'
    $catalog = Get-ODSSparkCatalog $root
    $pin = Get-ODSNativeLlamaPin -SourceRoot $root -Backend nvidia -Architecture arm64
    $testArtifact = Get-ODSSparkTestArtifact $catalog
    $top = Test-ODSCatalogModelArtifacts -Model (Get-CatalogModelById -Catalog $catalog -ModelId 'qwen3.6-35b-a3b-ud-q4')
    $topFile = [string]@($top)[0].File
    Check ($testArtifact.File -ceq 'Qwen3.5-2B-Q4_K_M.gguf' -and $testArtifact.Url -cmatch '^https://huggingface\.co/' -and $testArtifact.Sha256 -cmatch '^[0-9a-f]{64}$' -and
        $topFile -ceq 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf') 'the GPU check uses the pinned 2B catalog model, and the ladder floor is 35B-A3B'
    $devices = @(ConvertFrom-ODSNativeLlamaDeviceList ([IO.File]::ReadAllText((Join-Path $llamaFixtures 'llama-list-devices-cuda.txt'))))
    $runtimeDir = Join-Path (Get-ODSNativeLlamaRoot) 'b11429-win-cuda-13.4-arm64'
    $runtime = [pscustomobject]@{ Directory = $runtimeDir; ExecutablePath = (Join-Path $runtimeDir 'llama-server.exe'); ReleaseTag = 'b11429'
        ZipSha256 = $pin.Sha256; FileCount = 13; Flavor = $pin.Flavor }
    $goodCheck = [pscustomobject]@{ Passed = $true; Content = '4'; Archs = @('860', '890', '1200', '1210'); Class = ''; Reason = '' }
    $badArchs = Get-ODSNativeLlamaCheckVerdict '4' @(Get-ODSNativeLlamaCudaArchs ([IO.File]::ReadAllText((Join-Path $llamaFixtures 'cuda-archs-bad.txt')))) '12.1' $pin.RequiredCudaArchs
    $badAnswer = Get-ODSNativeLlamaCheckVerdict ((([IO.File]::ReadAllText((Join-Path $llamaFixtures 'generation-bad.json')) | ConvertFrom-Json).choices)[0].message.content) @('1210') '12.1' $pin.RequiredCudaArchs
    Check ($badArchs.Class -ceq 'build' -and $badAnswer.Class -ceq 'generation') 'the failing GPU checks come from the real GB10 captures (CUDA 12.8 archs, "????")'
    function New-Build([bool]$Device = $true) {
        return [pscustomobject]@{ Build = 11429; Commit = 'd81235049'; Devices = $script:devices; Device = $(if ($Device) { $script:devices[0] } else { $null })
            PolicyHint = ''; Backend = 'cuda' }
    }
    function New-Check($Verdict) { return [pscustomobject]@{ Passed = $false; Content = '????'; Archs = @('1210'); Class = $Verdict.Class; Reason = $Verdict.Reason } }
    function New-RunFailure([int]$ExitCode, [string]$Class) {
        return (New-ODSNativeLlamaFailure ("llama-server.exe --version failed with exit code $ExitCode. " + (Get-ODSNativeLlamaExitHint $ExitCode 'cuda' 'arm64')).Trim() $Class)
    }
    $none = [pscustomobject]@{ Kind = 'none'; Task = $null; Launch = $null; WasEnabled = $false; Launches = @(); Selection = $null }
    function New-Existing([string]$GgufFile = $topFile, [int]$ContextSize = 131072, [int]$Port = 8080) {
        return [pscustomobject]@{ Kind = 'llama-server'; Task = $null; WasEnabled = $true; Launches = @()
            Launch = [pscustomobject]@{ TaskName = 'ODSLlamaServerRuntime-S-1-5-21-1'; ExecutablePath = (Join-Path $runtimeDir 'llama-server.exe'); Port = $Port }
            Selection = [pscustomobject]@{ Port = $Port; GgufFile = $GgufFile; ContextSize = $ContextSize; MmprojFile = $null } }
    }
    function New-Foreign([string]$State = 'Disabled', [string]$Listener = '') {
        return [pscustomobject]@{ Kind = 'foreign'; Task = $null; Launch = $null; WasEnabled = ($State -ne 'Disabled'); Launches = @(); Selection = $null
            TaskName = 'ODSLlamaServerRuntime-S-1-5-21-1'; State = $State; Running = ($State -eq 'Running'); Port = 8080
            Listener = $(if ($Listener) { $Listener } else { $null }); Executable = 'C:\Engines\other-engine.exe'; Refusal = 'fixture'
            Replaceable = ($State -eq 'Disabled' -and -not $Listener) }
    }
    $modelsDir = Get-ODSPortalModelsDir
    function Reset-Spark {
        param([string]$Layout = 'memory-raised-96.json', [hashtable]$Change = @{ FreeGiB = [pscustomobject]@{ 'C:' = 300 } })
        $script:calls = [Collections.Generic.List[string]]::new()
        $script:prompts = [Collections.Generic.List[string]]::new()
        $script:answers = [Collections.Generic.Queue[object]]::new()
        $script:output = [Collections.Generic.List[string]]::new()
        $script:linux = $null
        $script:cutover = $null
        $script:wingetCalls = [Collections.Generic.List[string]]::new()
        $script:s = @{ Existing = $none; Layout = $Layout; Change = $Change; WslPresent = $true; WslReady = $true; FreeGB = 200
            RuntimeInstalled = $false; DockerInstalled = $true; Winget = $true; WingetCode = 0
            Builds = [Collections.Generic.Queue[object]]::new(); Checks = [Collections.Generic.Queue[object]]::new()
            Gpu = $null; Commit = $null; PageFileResult = [pscustomobject]@{ Result = 'set'; ExitCode = 0 }
            EnvGrep = [pscustomobject]@{ Code = 2; Output = '' }; ContainerIds = @(); DockerStopCode = 0
            CutoverFailure = ''; LinuxCode = 0; Host = $measuredHost }
        foreach ($path in @((Join-Path $env:LOCALAPPDATA 'ODS'), (Join-Path $env:USERPROFILE '.wslconfig'))) {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        }
        $null = New-Item -ItemType Directory -Path $modelsDir -Force
    }
    function Add-Answers([bool[]]$Answers) { foreach ($answer in $Answers) { $script:answers.Enqueue($answer) } }
    function Set-RuntimeInstalled { $script:s.RuntimeInstalled = $true; $null = New-Item -ItemType Directory -Path $script:runtimeDir -Force }
    function Add-StoreFile([string]$Name) { [IO.File]::WriteAllText((Join-Path $script:modelsDir $Name), 'gguf') }

    $taskName = 'ODSLlamaServerRuntime-S-1-5-21-1'
    $portalRuntime = Get-ODSPortalRuntimeDir
    $pwshPath = 'C:\Program Files\PowerShell\7\pwsh.exe'
    function Reset-PortalRuntime([hashtable]$Files) {
        if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'ODS')) { Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'ODS') -Recurse -Force }
        $null = New-Item -ItemType Directory -Path $portalRuntime -Force
        foreach ($name in $Files.Keys) { [IO.File]::WriteAllText((Join-Path $portalRuntime $name), $Files[$name]) }
    }
    $foreignPlan = (@{ ExecutablePath = 'C:\Engines\other-engine.exe'; Port = 8080; ModelsDir = 'C:\Engines\models'; ContextSize = 131072
        GgufFile = 'engine-model.gguf' } | ConvertTo-Json -Compress)

    # --- A model task ODS does not manage, read on the RTX Spark route only ---------
    & {
        $launcherAction = [pscustomobject]@{ Execute = $pwshPath; Arguments = (Get-ODSPortalLauncherArguments); WorkingDirectory = $portalRuntime }
        $script:taskState = 'Disabled'
        $script:listener = $null
        function Get-ODSPortalRuntimeTask { return [pscustomobject]@{ TaskName = $taskName; TaskPath = '\'; State = $script:taskState; Actions = @($launcherAction) } }
        function Get-ODSPortalLemonadeLaunches { return @() }
        function Get-Command { param([Parameter(Position = 0)][string[]]$Name, $CommandType) return @([pscustomobject]@{ Name = 'pwsh.exe'; Source = $pwshPath }) }
        function Get-ODSPortalPortOwner([int]$Port) { return $script:listener }
        Reset-PortalRuntime @{ 'runtime.json' = $foreignPlan }
        $found = Get-ODSPortalExistingRuntime -AllowForeign
        Check ($found.Kind -ceq 'foreign' -and $found.Replaceable -and -not $found.WasEnabled -and $found.TaskName -ceq $taskName -and $found.Port -eq 8080 -and
            $found.Executable -ceq 'C:\Engines\other-engine.exe' -and $null -eq $found.Selection) 'a disabled task whose plan names another engine is a replaceable foreign runtime, with its executable and port'
        $script:taskState = 'Ready'
        $found = Get-ODSPortalExistingRuntime -AllowForeign
        Check (-not $found.Replaceable -and $found.WasEnabled) 'an enabled foreign task is never replaceable'
        $script:taskState = 'Disabled'; $script:listener = 'other-engine'
        $found = Get-ODSPortalExistingRuntime -AllowForeign
        Check (-not $found.Replaceable -and $found.Listener -ceq 'other-engine') 'a disabled foreign task whose port still has a listener is not replaceable'
        $script:listener = $null
        Check ((Get-Failure { $null = Get-ODSPortalExistingRuntime }) -ceq 'The saved Portal runtime plan does not match the managed llama-server; no process was stopped.') 'without -AllowForeign (the AMD route, the controller) the same task is refused with the same text as before'
        Reset-PortalRuntime @{}
        $found = Get-ODSPortalExistingRuntime -AllowForeign
        Check ($found.Kind -ceq 'foreign' -and $found.Port -eq 0 -and $found.Executable -ceq $pwshPath -and $found.Replaceable -and
            (Get-Failure { $null = Get-ODSPortalExistingRuntime }) -match "^The Portal model runtime task $taskName has no saved runtime plan") 'a task left without its plan is foreign on the Spark route and keeps its old refusal elsewhere'
        $odsPlan = (@{ ExecutablePath = (Join-Path $runtimeDir 'llama-server.exe'); Port = 8080; ModelsDir = $modelsDir; ContextSize = 131072; GgufFile = $topFile } | ConvertTo-Json -Compress)
        Reset-PortalRuntime @{ 'runtime.json' = $odsPlan }
        $found = Get-ODSPortalExistingRuntime -AllowForeign
        Check ($found.Kind -ceq 'llama-server' -and $found.Selection.GgufFile -ceq $topFile) 'ODS''s own llama-server task is never foreign'
        function Get-ODSPortalRuntimeTask { throw 'The Portal model runtime task belongs to a different Windows user.' }
        Check ((Get-Failure { $null = Get-ODSPortalExistingRuntime -AllowForeign }) -ceq 'The Portal model runtime task belongs to a different Windows user.') 'other refusals still stop setup on the Spark route'
    }

    # --- Every file of a model, verified, resumable ---------------------------------------
    & {
        function Get-Bytes([string]$Text) { return [Text.Encoding]::ASCII.GetBytes($Text) }
        function Get-Sha([byte[]]$Bytes) { $hash = [Security.Cryptography.SHA256]::Create(); try { return (-join ($hash.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $hash.Dispose() } }
        $one = Get-Bytes ('part one ' * 64)
        $two = Get-Bytes ('part two ' * 32)
        $script:served = @{ 'https://huggingface.co/org/repo/resolve/0123/Big-00001-of-00002.gguf' = $one; 'https://huggingface.co/org/repo/resolve/0123/Big-00002-of-00002.gguf' = $two }
        $parts = @(
            [pscustomobject]@{ File = 'Big-00001-of-00002.gguf'; Url = 'https://huggingface.co/org/repo/resolve/0123/Big-00001-of-00002.gguf'; Sha256 = (Get-Sha $one); SizeBytes = [long]$one.Length },
            [pscustomobject]@{ File = 'Big-00002-of-00002.gguf'; Url = 'https://huggingface.co/org/repo/resolve/0123/Big-00002-of-00002.gguf'; Sha256 = (Get-Sha $two); SizeBytes = [long]$two.Length })
        $script:curlCalls = [Collections.Generic.List[string]]::new()
        $script:curlCode = 0
        $script:corrupt = $false
        $script:short = $false
        function curl.exe {
            $script:curlCalls.Add($args -join ' ')
            $output = $args[[Array]::IndexOf($args, '--output') + 1]
            $script:partialSeen = Test-Path -LiteralPath $output
            $bytes = [byte[]]$script:served[[string]$args[-1]]
            if ($script:corrupt) { $bytes = [byte[]]$bytes.Clone(); $bytes[0] = 0 }
            if ($script:short) { $bytes = [byte[]]$bytes[0..10] }
            # --continue-at - appends what an earlier run did not get.
            $have = if (Test-Path -LiteralPath $output) { [IO.File]::ReadAllBytes($output).Length } else { 0 }
            $stream = [IO.File]::Open($output, [IO.FileMode]::Append)
            try { if ($bytes.Length -gt $have) { $stream.Write($bytes, $have, $bytes.Length - $have) } } finally { $stream.Dispose() }
            $global:LASTEXITCODE = $script:curlCode
        }
        Reset-PortalRuntime @{}
        $first = Join-Path $modelsDir 'Big-00001-of-00002.gguf'
        $second = Join-Path $modelsDir 'Big-00002-of-00002.gguf'
        $null = Save-ODSPortalModelArtifacts $parts
        Check ($script:curlCalls.Count -eq 2 -and $script:curlCalls[0] -match '^--fail --location --proto =https --proto-redir =https --continue-at - --output .+Big-00001-of-00002\.gguf\.partial https://huggingface\.co/' -and
            (Get-Sha ([IO.File]::ReadAllBytes($first))) -ceq $parts[0].Sha256 -and (Get-Sha ([IO.File]::ReadAllBytes($second))) -ceq $parts[1].Sha256 -and
            @(Get-ChildItem -LiteralPath $modelsDir -Filter '*.partial').Count -eq 0) 'every part of a split model is downloaded over HTTPS, resumable, checked and moved into place'
        $script:curlCalls.Clear()
        $null = Save-ODSPortalModelArtifacts $parts
        Check ($script:curlCalls.Count -eq 0) 'parts already in the store with their SHA-256 are not downloaded again'
        Remove-Item -LiteralPath $second
        [IO.File]::WriteAllBytes("$second.partial", [byte[]]$two[0..99])
        $null = Save-ODSPortalModelArtifacts $parts
        Check ($script:curlCalls.Count -eq 1 -and $script:partialSeen -and (Get-Sha ([IO.File]::ReadAllBytes($second))) -ceq $parts[1].Sha256) 'a stopped download resumes from its .partial file'
        Remove-Item -LiteralPath $second
        [IO.File]::WriteAllBytes("$second.partial", [byte[]](@($two) + @($two)))
        $script:curlCalls.Clear()
        $null = Save-ODSPortalModelArtifacts $parts
        Check ($script:curlCalls.Count -eq 1 -and -not $script:partialSeen -and (Get-Sha ([IO.File]::ReadAllBytes($second))) -ceq $parts[1].Sha256) 'a partial longer than the file is discarded before the download starts again'
        Remove-Item -LiteralPath $second
        $script:corrupt = $true
        $message = Get-Failure { $null = Save-ODSPortalModelArtifacts $parts }
        Check ($message -match '^The downloaded Big-00002-of-00002\.gguf does not match its checksum \([0-9a-f]{64}, expected [0-9a-f]{64}\); it was deleted\. Rerun the same command\.$' -and
            -not (Test-Path -LiteralPath $second) -and -not (Test-Path -LiteralPath "$second.partial")) 'a part that fails its SHA-256 is deleted, never kept under its name'
        $script:corrupt = $false; $script:short = $true
        $message = Get-Failure { $null = Save-ODSPortalModelArtifacts $parts }
        Check ($message -match "^The downloaded Big-00002-of-00002\.gguf is 11 bytes, not the $($two.Length) the ODS catalog pins; it was deleted\.") 'a part of the wrong size is refused before hashing'
        $script:short = $false; $script:curlCode = 22
        $message = Get-Failure { $null = Save-ODSPortalModelArtifacts $parts }
        Check ($message -ceq 'Downloading Big-00002-of-00002.gguf failed (curl exit 22). Check the internet connection, then rerun the same command to resume it.' -and
            (Test-Path -LiteralPath "$second.partial")) 'a failed download keeps its .partial for the next run'
        $script:curlCode = 0
        $script:curlCalls.Clear()
        foreach ($case in @(
            @{ Name = 'an address that is not a Hugging Face file'; Change = @{ Url = 'https://example.com/Big-00002-of-00002.gguf' }; Pattern = '^Refusing to download Big-00002-of-00002\.gguf from an address that is not a Hugging Face file' },
            @{ Name = 'plain HTTP'; Change = @{ Url = 'http://huggingface.co/org/repo/x.gguf' }; Pattern = 'not a Hugging Face file' },
            @{ Name = 'a file without a pinned SHA-256'; Change = @{ Sha256 = '' }; Pattern = 'has no pinned SHA-256 in the ODS catalog, so it cannot be verified; nothing was downloaded\.$' },
            @{ Name = 'a path instead of a file name'; Change = @{ File = '..\Big.gguf' }; Pattern = '^The ODS catalog names a model file ODS cannot store safely' })) {
            $bad = $parts[1].PSObject.Copy()
            foreach ($key in $case.Change.Keys) { $bad.$key = $case.Change[$key] }
            Remove-Item -LiteralPath $first
            $message = Get-Failure { $null = Save-ODSPortalModelArtifacts @($parts[0], $bad) }
            Check ($message -match $case.Pattern -and $script:curlCalls.Count -eq 0) "$($case.Name) is refused before any file is downloaded"
            [IO.File]::WriteAllBytes($first, $one)
        }
    }

    # --- The cutover: replacing a disabled foreign task, and its rollback ----------------
    $foreignFiles = @{ 'runtime.json' = $foreignPlan; 'launch.ps1' = 'engine launcher'; 'engine-settings.json' = '{"engine":true}' }
    $exportedXml = '<?xml version="1.0" encoding="UTF-16"?><Task><Settings><Enabled>false</Enabled></Settings></Task>'
    & {
        $script:events = [Collections.Generic.List[string]]::new()
        $script:current = New-Foreign 'Disabled'
        $script:failStart = $false
        $script:reregistered = ''
        function Enter-ODSPortalRuntimeChange([hashtable]$Lock) { }
        function Exit-ODSPortalRuntimeChange([hashtable]$Lock) { }
        function Get-ODSPortalExistingRuntime { param([switch]$AllowForeign) if (-not $AllowForeign) { throw 'a foreign runtime is read with -AllowForeign' }; return $script:current }
        function Export-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add("export:$TaskName"); return $exportedXml }
        function Register-ODSPortalRuntimeTask($Registration, [string]$Description = '') { $script:events.Add('register'); return $taskName }
        function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add('start') }
        function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = 0) { $script:events.Add('wait'); if ($script:failStart) { throw 'fixture: llama-server exited' }; return 'model' }
        function Stop-ODSPortalRuntime([string]$ExecutablePath = '') { $script:events.Add('stop') }
        function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm, $ErrorAction) $script:events.Add('unregister') }
        function Register-ScheduledTask { param($Xml, $TaskName, $TaskPath, [switch]$Force, $ErrorAction) $script:events.Add("reregister:$TaskName"); $script:reregistered = $Xml }
        function Disable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add('disable') }
        function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) return [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory } }
        function Get-Command { param([Parameter(Position = 0)][string[]]$Name, $CommandType) return @([pscustomobject]@{ Name = 'pwsh.exe'; Source = $pwshPath }) }
        function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }
        $options = New-ODSPortalRuntimeOptions $runtime ([pscustomobject]@{ Device = $devices[0] }) 'cuda' 'arm64'
        $plan = [ordered]@{ ExecutablePath = $runtime.ExecutablePath; Port = 8080; ModelsDir = $modelsDir; ContextSize = 131072; GgufFile = $topFile }
        $memory = New-ODSPortalMemoryPlanRecord $plan ([pscustomobject]@{ DeviceNeedGiB = 23.83; CommitNeedGiB = 25.8; Source = 'catalog' }) 15.25
        $cutover = @{ Existing = $script:current; Plan = $plan; Options = $options; ApiKey = ('a' * 64); KeepPort = $false; WslDistro = 'Ubuntu-24.04'
            WslInstallDir = '/home/user/ods'; TaskDescription = $script:ODSPortalRuntimeTaskDescriptions['cuda']; MemoryPlan = $memory }
        Reset-PortalRuntime $foreignFiles
        $script:output.Clear()
        $null = Invoke-ODSPortalRuntimeCutover @cutover
        $replacedDir = Get-ODSPortalReplacedTasksDir
        $xml = @(Get-ChildItem -LiteralPath $replacedDir -Filter "$taskName-*.xml")
        $copies = @(Get-ChildItem -LiteralPath $replacedDir -Directory -Filter 'portal-runtime-*')
        $saved = if ($copies.Count) { [IO.File]::ReadAllText((Join-Path $copies[0].FullName 'runtime.json')) } else { '' }
        $now = Get-Content -LiteralPath (Join-Path $portalRuntime 'runtime.json') -Raw | ConvertFrom-Json
        Check ($xml.Count -eq 1 -and [IO.File]::ReadAllText($xml[0].FullName, [Text.Encoding]::Unicode) -ceq $exportedXml -and $copies.Count -eq 1 -and $saved -ceq $foreignPlan -and
            (Test-Path -LiteralPath (Join-Path $copies[0].FullName 'engine-settings.json'))) 'before replacing it, the foreign task''s definition (UTF-16 XML) and its whole runtime folder are kept under replaced-tasks'
        Check (($script:events -join ',') -ceq "export:$taskName,register,start,wait" -and $now.ExecutablePath -ceq $runtime.ExecutablePath -and
            (Get-Content -LiteralPath (Join-Path $portalRuntime 'memory-plan.json') -Raw | ConvertFrom-Json).CommitNeedGiB -eq 25.8 -and
            @(Get-ChildItem -LiteralPath (Get-ODSPortalStateDir) -Directory -Filter 'portal-runtime.backup-*').Count -eq 0 -and
            (Get-Output).Contains("ODS replaced the model task $taskName. Its definition is kept in $($xml[0].FullName) and its files in $($copies[0].FullName).")) 'ODS''s task and plan replace it, and setup says where the saved copy is'
        Reset-PortalRuntime $foreignFiles
        $script:events.Clear()
        $script:failStart = $true
        $message = Get-Failure { $null = Invoke-ODSPortalRuntimeCutover @cutover }
        Check ($message -ceq "The llama.cpp runtime did not start: fixture: llama-server exited The model task $taskName is back as it was (disabled), with its files." -and
            ($script:events -join ',') -ceq "export:$taskName,register,start,wait,stop,unregister,reregister:$taskName,disable" -and $script:reregistered -ceq $exportedXml) 'a failed cutover stops ODS''s new task, registers the saved definition again and disables it'
        Check ([IO.File]::ReadAllText((Join-Path $portalRuntime 'runtime.json')) -ceq $foreignPlan -and
            [IO.File]::ReadAllText((Join-Path $portalRuntime 'launch.ps1')) -ceq 'engine launcher' -and
            -not (Test-Path -LiteralPath (Join-Path $portalRuntime 'memory-plan.json'))) 'its runtime folder is back byte for byte, without ODS''s memory plan'
        Reset-PortalRuntime $foreignFiles
        $script:events.Clear()
        $script:failStart = $false
        $script:current = New-Foreign 'Disabled' 'other-engine'
        $message = Get-Failure { $null = Invoke-ODSPortalRuntimeCutover @cutover }
        Check ($message -ceq "The model task $taskName or its server started while setup was running, so setup did not replace it; nothing was changed. Stop and disable it again, then rerun setup." -and
            $script:events.Count -eq 0 -and -not (Test-Path -LiteralPath (Get-ODSPortalReplacedTasksDir))) 'a foreign server that started meanwhile is not replaced, and nothing is saved or changed'
    }

    # --- The cutover keeps runtime.json and memory-plan.json together ------------------
    $oldPlan = (@{ ExecutablePath = $runtime.ExecutablePath; Port = 8080; ModelsDir = $modelsDir; ContextSize = 65536; GgufFile = 'Qwen3.5-9B-Q4_K_M.gguf'
        WslDistro = 'Ubuntu-24.04'; WslInstallDir = '/home/user/ods' } | ConvertTo-Json -Compress)
    $oldMemory = '{"GgufFile":"Qwen3.5-9B-Q4_K_M.gguf","ContextSize":65536,"DeviceNeedGiB":7.77,"CommitNeedGiB":9.34,"WslGiB":15.25,"Source":"catalog"}'
    & {
        $script:events = [Collections.Generic.List[string]]::new()
        $script:waits = 0
        $odsExisting = [pscustomobject]@{ Kind = 'llama-server'; Task = $null; WasEnabled = $true; Launches = @()
            Launch = [pscustomobject]@{ TaskName = $taskName; ExecutablePath = $runtime.ExecutablePath; Port = 8080; Plan = ($oldPlan | ConvertFrom-Json) }
            Selection = [pscustomobject]@{ Port = 8080; GgufFile = 'Qwen3.5-9B-Q4_K_M.gguf'; ContextSize = 65536; MmprojFile = $null } }
        function Enter-ODSPortalRuntimeChange([hashtable]$Lock) { }
        function Exit-ODSPortalRuntimeChange([hashtable]$Lock) { }
        function Get-ODSPortalExistingRuntime { param([switch]$AllowForeign) return $odsExisting }
        function Stop-ODSPortalRuntime([string]$ExecutablePath = '') { $script:events.Add('stop') }
        function Register-ODSPortalRuntimeTask($Registration, [string]$Description = '') { $script:events.Add('register'); return $taskName }
        function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add('start') }
        function Enable-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction) $script:events.Add('enable') }
        function Assert-ODSPortalPortAvailable([int]$Port) { $script:events.Add("port:$Port") }
        function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = 0) {
            $script:waits++
            $script:events.Add('wait')
            # The new plan fails; the restored one proves itself again.
            if ($script:waits -eq 1) { throw 'fixture: the new model did not load' }
            return 'model'
        }
        function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) return [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory } }
        function Get-Command { param([Parameter(Position = 0)][string[]]$Name, $CommandType) return @([pscustomobject]@{ Name = 'pwsh.exe'; Source = $pwshPath }) }
        function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }
        $options = New-ODSPortalRuntimeOptions $runtime ([pscustomobject]@{ Device = $devices[0] }) 'cuda' 'arm64'
        $plan = [ordered]@{ ExecutablePath = $runtime.ExecutablePath; Port = 8080; ModelsDir = $modelsDir; ContextSize = 131072; GgufFile = $topFile }
        $memory = New-ODSPortalMemoryPlanRecord $plan ([pscustomobject]@{ DeviceNeedGiB = 23.83; CommitNeedGiB = 25.8; Source = 'catalog' }) 15.25
        Reset-PortalRuntime @{ 'runtime.json' = $oldPlan; 'memory-plan.json' = $oldMemory; 'intent.json' = '{"State":"running"}' }
        $message = Get-Failure { $null = Invoke-ODSPortalRuntimeCutover -Existing $odsExisting -Plan $plan -Options $options -ApiKey ('a' * 64) -KeepPort $true `
            -WslDistro 'Ubuntu-24.04' -WslInstallDir '/home/user/ods' -TaskDescription $script:ODSPortalRuntimeTaskDescriptions['cuda'] -MemoryPlan $memory }
        Check ($message -ceq 'The llama.cpp runtime did not start: fixture: the new model did not load The previous ODS runtime was restored and is running again.' -and
            [IO.File]::ReadAllText((Join-Path $portalRuntime 'runtime.json')) -ceq $oldPlan -and [IO.File]::ReadAllText((Join-Path $portalRuntime 'memory-plan.json')) -ceq $oldMemory -and
            ($script:events -join ',') -ceq 'stop,port:8080,register,start,wait,stop,enable,start,wait') 'the new memory plan is written after the backup: a failed start puts back the old runtime.json and memory-plan.json together and restarts the old model'
        # The deferred GPU check fails after the current model stopped.
        Reset-PortalRuntime @{ 'runtime.json' = $oldPlan; 'memory-plan.json' = $oldMemory; 'intent.json' = '{"State":"running"}' }
        $script:events.Clear(); $script:waits = 1
        $check = { param($Argument) $script:events.Add("check:$Argument"); $failure = [Management.Automation.RuntimeException]::new('llama.cpp b11429 has CUDA kernels for 1200, not for this GPU.'); $failure.Data['gpuCheck'] = $true; throw $failure }
        $message = Get-Failure { $null = Invoke-ODSPortalRuntimeCutover -Existing $odsExisting -Plan $plan -Options $options -ApiKey ('a' * 64) -KeepPort $true `
            -WslDistro 'Ubuntu-24.04' -WslInstallDir '/home/user/ods' -MemoryPlan $memory -BeforeStart $check -BeforeStartArgument 'deferred' }
        Check ($message -ceq 'llama.cpp b11429 has CUDA kernels for 1200, not for this GPU. The previous ODS runtime was restored and is running again.' -and
            ($script:events -join ',') -ceq 'stop,check:deferred,enable,start,wait' -and [IO.File]::ReadAllText((Join-Path $portalRuntime 'runtime.json')) -ceq $oldPlan -and
            [IO.File]::ReadAllText((Join-Path $portalRuntime 'memory-plan.json')) -ceq $oldMemory) 'a deferred check runs once the current model stopped; its failure restarts that model unchanged and keeps the check''s own text'
    }

    # Every host boundary is a fixture from here on.
    function Get-ODSSparkHost { $script:calls.Add('spark-host'); return $script:s.Host }
    function Get-ODSPortalExistingRuntime { param([switch]$AllowForeign) $script:calls.Add("existing:$([bool]$AllowForeign)"); return $script:s.Existing }
    function Get-ODSSparkMemoryLayout {
        param([long]$CarveOutMiB, [string]$ModelsDir)
        $script:calls.Add('layout')
        $layout = Get-Content -LiteralPath (Join-Path $script:fixtures $script:s.Layout) -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in $script:s.Change.Keys) { $layout.$key = $script:s.Change[$key] }
        return $layout
    }
    function Get-ODSPortalFreeSystemGB { $script:calls.Add('disk-check'); return $script:s.FreeGB }
    function Test-ODSPortalVirtualization { $script:calls.Add('virt-check'); return $true }
    function Test-ODSPortalAdministrator { return $false }
    function Test-ODSNativeWindowsInstall { return $false }
    function Get-Command {
        param([Parameter(Position = 0)][string[]]$Name, $CommandType)
        $found = @()
        foreach ($item in $Name) {
            switch ($item) {
                'wsl.exe' { if ($script:s.WslPresent) { $found += [pscustomobject]@{ Name = 'wsl.exe'; Source = 'C:\Windows\System32\wsl.exe' } } }
                'winget.exe' { if ($script:s.Winget) { $found += [pscustomobject]@{ Name = 'winget.exe'; Source = 'winget-fixture' } } }
                'pwsh.exe' { $found += [pscustomobject]@{ Name = 'pwsh.exe'; Source = 'C:\Program Files\PowerShell\7\pwsh.exe' } }
                'powershell.exe' { }
                default { throw "unexpected command lookup: $item" }
            }
        }
        return $found
    }
    function winget-fixture { $script:wingetCalls.Add($args -join ' '); $global:LASTEXITCODE = $script:s.WingetCode }
    function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
        $script:calls.Add('confirm')
        $script:prompts.Add($Message)
        if ($NonInteractive) { return $false }
        if (-not $script:answers.Count) { throw "unexpected prompt: $Message" }
        return [bool]$script:answers.Dequeue()
    }
    function Invoke-ODSPortalWsl([string[]]$Arguments) {
        $key = $Arguments -join ' '
        $script:calls.Add("wsl:$key")
        $code = 0; $text = ''
        switch -Regex ($key) {
            '^--status$' { if (-not $script:s.WslReady) { $code = 1 }; break }
            '^--version$' { $text = "WSL version: 2.6.1.0`nKernel version: 6.6.87.2"; break }
            '^--list --quiet$' { $text = 'Ubuntu-24.04'; break }
            '^--list --verbose$' { $text = '* Ubuntu-24.04    Running    2'; break }
            '--exec cat /etc/os-release$' { $text = "NAME=`"Ubuntu`"`nID=ubuntu`nVERSION_ID=`"24.04`""; break }
            '--exec id -u$' { $text = '1000'; break }
            '--exec cat /proc/1/comm$' { $text = 'systemd'; break }
            '--exec docker info$' { break }
            '--exec docker compose version$' { break }
            '--exec printenv HOME$' { $text = '/home/user'; break }
            '^--distribution Ubuntu-24\.04 --exec grep -E \^\(GPU_BACKEND\|NATIVE_LLM_BASE_URL\)= /home/user/ods/\.env$' {
                $code = $script:s.EnvGrep.Code; $text = $script:s.EnvGrep.Output; break
            }
            '^--distribution Ubuntu-24\.04 --exec docker ps --quiet --filter label=com\.docker\.compose\.project=ods --filter label=com\.docker\.compose\.service=llama-server$' {
                $text = (@($script:s.ContainerIds) -join "`n"); break
            }
            '^--distribution Ubuntu-24\.04 --exec docker stop ' { $code = $script:s.DockerStopCode; break }
            '^--distribution Ubuntu-24\.04 --exec docker start ' { break }
            '^--shutdown$' { break }
            default { throw "Unexpected WSL call: $key" }
        }
        return [pscustomobject]@{ Code = $code; Output = $text; Error = $(if ($code -and $code -notin @(1, 2)) { 'fixture failure' } else { '' }) }
    }
    function Get-ODSSparkInstalledRuntime($Pin) { if ($script:s.RuntimeInstalled) { return $script:runtime }; return $null }
    function Install-ODSNativeLlamaRuntime { param($Pin, $Root) $script:calls.Add('runtime-install'); Set-RuntimeInstalled; return $script:runtime }
    function Get-ODSNativeLlamaCodeIntegrityState { return [pscustomobject]@{ SmartAppControl = 'off'; UserModeCodeIntegrity = 'off' } }
    function Test-ODSNativeLlamaQualification {
        param($ExecutablePath, $ExpectedBuild, $AdapterName, $CodeIntegrity, $Backend, $Architecture)
        $script:calls.Add('qualify-build')
        if ($Backend -cne 'cuda' -or $Architecture -cne 'arm64' -or $ExpectedBuild -ne 11429) { throw 'the RTX Spark build is qualified as CUDA arm64 b11429' }
        $next = if ($script:s.Builds.Count) { $script:s.Builds.Dequeue() } else { New-Build }
        if ($next -is [Exception]) { throw $next }
        return $next
    }
    function Get-ODSNativeReasoningArgs { param($Executable, $Mode, $FallbackFormat) return @('--reasoning', 'off') }
    function Select-ODSPortalCheckPort { return 18090 }
    function Invoke-ODSNativeLlamaGenerationProbe {
        param($ExecutablePath, $ModelPath, $Device, $Port, $LogPath, $ComputeCap, $ArchsByCompute, $ReasoningArguments, $Architecture, $CodeIntegrity, $TimeoutSeconds)
        $script:calls.Add("gpu-check:$Device")
        if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) { throw "the GPU check model is missing: $ModelPath" }
        if ($script:s.Checks.Count) { return $script:s.Checks.Dequeue() }
        return $script:goodCheck
    }
    function Save-ODSPortalModelArtifacts([object[]]$Artifacts) {
        foreach ($artifact in @($Artifacts)) {
            Assert-ODSPortalModelArtifact $artifact
            $path = Join-Path $script:modelsDir $artifact.File
            if (Test-Path -LiteralPath $path -PathType Leaf) { $script:calls.Add("verify:$($artifact.File)"); continue }
            $script:calls.Add("download:$($artifact.File)")
            [IO.File]::WriteAllText($path, 'gguf')
        }
        return $script:modelsDir
    }
    function Get-ODSNativeGpuMemoryState { param([string]$GpuName = '') return $script:s.Gpu }
    function Get-ODSNativeCommitState { return $script:s.Commit }
    function Set-ODSSparkPageFile([int]$SizeGiB) { $script:calls.Add("pagefile:$SizeGiB"); return $script:s.PageFileResult }
    function Write-ODSWslConfig([int]$GiB) { $script:calls.Add("wslconfig:$GiB"); return [pscustomobject]@{ Result = 'written'; Path = 'C:\Users\fixture\.wslconfig' } }
    function Test-ODSSparkWslPresent { return $script:s.WslPresent }
    function Get-ODSPortalDockerDesktop { return [pscustomobject]@{ Installed = $script:s.DockerInstalled; Exe = 'docker-desktop.exe'; Cli = 'docker.exe' } }
    function Install-ODSPortalDockerDesktop {
        $script:calls.Add('docker-install'); $script:s.DockerInstalled = $true
        return [pscustomobject]@{ AlreadyInstalled = $false; Desktop = (Get-ODSPortalDockerDesktop) }
    }
    function Test-ODSPortalDockerEngine($Desktop) { return $true }
    function Start-ODSPortalDockerDesktop($Desktop) { $script:calls.Add('docker-start') }
    function Install-ODSPortalWslFeatures([switch]$MissingExecutable) { $script:calls.Add('wsl-features'); return 0 }
    function Register-ODSPortalResume([string]$InstallerRoot, [System.Collections.IDictionary]$Options) { $script:calls.Add('resume') }
    function Invoke-ODSPortalLinuxInstaller {
        param([string]$InstallerRoot, [string]$Distro, [string[]]$LinuxArguments, [string]$InstallRoot, [bool]$OpenPortal, [string]$DockerDesktopPath = '',
            [string]$StateRoot = '', [string[]]$NewInstallationArguments = @(), [System.Collections.IDictionary]$ForwardEnvironment = @{})
        $script:calls.Add('linux-installer')
        $script:linux = [pscustomobject]@{ Arguments = @($LinuxArguments); Environment = $ForwardEnvironment; InstallRoot = $InstallRoot }
        return $script:s.LinuxCode
    }
    function Get-ODSPortalWindowsNvidiaDriver { $script:calls.Add('nvidia-driver'); return 616 }
    function Assert-ODSPortalNvidiaReady([string]$Distro, $WindowsDriver) { $script:calls.Add('nvidia-gate') }
    function Get-ODSPortalAmdPlan([string]$SourceRoot) { $script:calls.Add('amd-plan'); return $null }
    function Invoke-ODSPortalRuntimeCutover {
        param($Existing, $Plan, $Options, [string]$ApiKey, [bool]$KeepPort, [string]$WslDistro = '', [string]$WslInstallDir = '',
            [string]$TaskDescription = '', $MemoryPlan = $null, [scriptblock]$BeforeStart = $null, $BeforeStartArgument = $null)
        $script:calls.Add('cutover')
        $script:cutover = [pscustomobject]@{ Existing = $Existing; Plan = $Plan; Options = $Options; ApiKey = $ApiKey; KeepPort = $KeepPort
            WslDistro = $WslDistro; WslInstallDir = $WslInstallDir; TaskDescription = $TaskDescription; MemoryPlan = $MemoryPlan
            BeforeStart = [bool]$BeforeStart }
        if ($BeforeStart) { & $BeforeStart $BeforeStartArgument }
        if ($script:s.CutoverFailure) { throw $script:s.CutoverFailure }
        return [pscustomobject]@{ Plan = $Plan }
    }
    function Select-ODSPortalRuntimePort { return 8080 }
    function Remove-ODSNativeLlamaOldRuntimes { param([string]$KeepDirectory, [string]$PreviousDirectory = '', [string]$Root = '') $script:calls.Add('old-runtimes') }
    function Invoke-Setup([hashtable]$Options = @{}) { return (Invoke-ODSPortalSetup $Options $installerRoot) }

    # --- A fresh RTX Spark goes native ----------------------------------------------
    Reset-Spark
    Add-Answers @($true)
    $code = Invoke-Setup
    $joined = @($script:linux.Arguments) -join ' '
    Check ($code -eq 0 -and $script:prompts.Count -eq 1 -and $script:prompts[0] -match '^Download llama\.cpp b11429 for NVIDIA GPUs on Windows on Arm \(CUDA 13\.4\) from github\.com/ggml-org \(about 285 MiB, SHA-256 pinned\), and a 1\.3 GB test model to check it on your GPU before the large model download\? It runs only on this computer \(127\.0\.0\.1\)\.$') 'a fresh RTX Spark asks once (M10) before it downloads llama.cpp and the test model'
    $order = @('existing:True', 'disk-check', 'layout', 'confirm', 'runtime-install', 'qualify-build', "download:$($testArtifact.File)", 'gpu-check:CUDA0',
        'wsl:--version', "download:$topFile", 'cutover', 'linux-installer')
    $positions = @($order | ForEach-Object { Get-Index $_ })
    Check (@($positions | Where-Object { $_ -lt 0 }).Count -eq 0 -and (($positions | Sort-Object) -join ',') -ceq ($positions -join ',')) 'stage 1 reads what ODS runs, runs the disk check, plans, then asks, downloads and checks llama.cpp; stage 3 downloads the model and cuts over before the Linux installer'
    Check ((Get-Index 'existing:True') -lt (Get-Index 'disk-check') -and (Get-Index 'disk-check') -lt (Get-Index 'layout') -and (Get-Index 'layout') -lt (Get-Index 'runtime-install')) 'nothing is downloaded before what ODS runs, the generic checks and the disk plan'
    Check (-not $script:calls.Contains('nvidia-driver') -and -not $script:calls.Contains('nvidia-gate') -and -not $script:calls.Contains('amd-plan')) 'an RTX Spark never runs the NVIDIA container gate or the AMD route'
    Check ($joined -match '--native-llm-url http://localhost:8080 --native-llm-host-transport model-router --native-llm-model Qwen3\.6-35B-A3B-UD-Q4_K_M\.gguf --native-llm-context-size 131072 --native-llm-gpu-name NVIDIA RTX Spark N1X \(6144-core Blackwell RTX GPU\) --native-llm-gpu-vram-mb 98240 --native-llm-api-key-env ODS_NATIVE_LLM_API_KEY --native-llm-runtime windows-native-cuda --tier 4$') 'the Linux installer gets the native route, the carve-out as GPU memory, the CUDA build policy and tier 4'
    $key = [string]$script:linux.Environment['ODS_NATIVE_LLM_API_KEY']
    Check ($key -cmatch '^[0-9a-f]{64}$' -and -not $joined.Contains($key) -and $script:linux.Environment.Count -eq 1 -and $script:cutover.ApiKey -ceq $key) 'the API key travels in the forwarded environment only, never in the arguments'
    Check ($script:cutover.TaskDescription -ceq 'ODS: llama.cpp model server for the NVIDIA GPU (127.0.0.1, API key). Starts at sign-in.' -and
        $script:cutover.Options.Backend -ceq 'cuda' -and $script:cutover.Options.Architecture -ceq 'arm64' -and $script:cutover.Options.NGpuLayers -ceq 'all' -and
        $script:cutover.Options.Device -ceq 'CUDA0' -and $script:cutover.Plan.GgufFile -ceq $topFile -and $script:cutover.Plan.ContextSize -eq 131072 -and
        $script:cutover.WslDistro -ceq 'Ubuntu-24.04' -and $script:cutover.WslInstallDir -ceq '/home/user/ods' -and -not $script:cutover.BeforeStart) 'the cutover starts the NVIDIA task with CUDA options, every layer on CUDA0, bound to the Ubuntu install'
    Check ($script:cutover.MemoryPlan.GgufFile -ceq $topFile -and $script:cutover.MemoryPlan.ContextSize -eq 131072 -and $script:cutover.MemoryPlan.DeviceNeedGiB -eq 23.83 -and
        $script:cutover.MemoryPlan.CommitNeedGiB -eq 25.8 -and $script:cutover.MemoryPlan.Source -ceq 'catalog') 'memory-plan.json carries the planned GPU and commit need (23.83 / 25.80 GiB)'
    $output = Get-Output
    Check ($output.Contains('NVIDIA RTX Spark N1X (6144-core Blackwell RTX GPU): 95.9 GiB reserved for the GPU, 30.5 GiB for Windows, page file 96.0 GiB, commit limit 126.5 GiB. Model plan: Qwen 3.6 35B-A3B, 128K context, about 23.8 GiB of GPU memory.') -and
        $output -match 'llama\.cpp b11429 passed its check on CUDA0' -and $output -match '\[3/4\]  NVIDIA GPU') 'stage 1 shows the memory split and the model plan (M1); stage 3 names the NVIDIA GPU route'
    $cache = Get-Content -LiteralPath (Join-Path $runtimeDir 'qualification.json') -Raw | ConvertFrom-Json
    Check ($cache.schemaVersion -eq 1 -and $cache.DriverVersion -ceq '616.62' -and $cache.PciDeviceId -ceq '0x2e03' -and $cache.Device.Name -ceq 'CUDA0' -and
        (@($cache.Archives) -join ',') -ceq (@($pin.Archives | ForEach-Object { $_.Sha256 }) -join ',') -and (@($cache.Archs) -join ',') -ceq '860,890,1200,1210') 'a passed check is cached with the archives, driver, PCI id and GPU it was made with'

    # --- The check is cached; a new driver checks again -------------------------------
    Reset-Spark
    Set-RuntimeInstalled
    $null = Invoke-Setup
    Check ($script:prompts.Count -eq 0 -and $script:calls.Contains('qualify-build') -and $script:calls.Contains('gpu-check:CUDA0') -and
        -not $script:calls.Contains('runtime-install')) 'an installed runtime with no cache is checked again without asking (first run after an update)'
    $script:calls.Clear()
    $null = Invoke-Setup
    Check (-not $script:calls.Contains('qualify-build') -and -not ($script:calls -like 'gpu-check:*') -and $script:cutover.Options.Device -ceq 'CUDA0' -and
        (Get-Output) -match 'already passed its check on CUDA0 with driver 616\.62') 'a rerun reuses the cached check: no llama.cpp run, the same CUDA device'
    $script:s.Host = $measuredHost.PSObject.Copy(); $script:s.Host.DriverVersion = '617.10'
    $script:calls.Clear()
    $null = Invoke-Setup
    Check ($script:calls.Contains('qualify-build') -and $script:calls.Contains('gpu-check:CUDA0')) 'a different NVIDIA driver misses the cache and is checked again'

    # --- A live model task ODS does not manage stops setup (M21, M21b) -------------
    foreach ($case in @(@{ State = 'Ready'; Listener = '' }, @{ State = 'Running'; Listener = 'other-engine' })) {
        Reset-Spark
        $script:s.Existing = New-Foreign $case.State $case.Listener
        $message = Get-Failure { $null = Invoke-Setup }
        Check ($message -match "^The Windows model task ODSLlamaServerRuntime-S-1-5-21-1 runs a model server this ODS release does not manage \(C:\\Engines\\other-engine\.exe\)\. Setup stopped before changing anything" -and
            $message.Contains("Stop-ScheduledTask -TaskPath '\' -TaskName 'ODSLlamaServerRuntime-S-1-5-21-1'; Disable-ScheduledTask -TaskPath '\' -TaskName 'ODSLlamaServerRuntime-S-1-5-21-1'") -and
            $message.Contains((Join-Path (Join-Path $env:LOCALAPPDATA 'ODS') 'replaced-tasks'))) "a $($case.State.ToLowerInvariant()) foreign model task stops setup with the steps to disable it and where its copy will go (M21)"
        Check ($script:calls.Count -eq 2 -and $script:calls[0] -ceq 'spark-host' -and $script:calls[1] -ceq 'existing:True' -and $script:prompts.Count -eq 0 -and
            -not (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'ODS\llama.cpp'))) 'it stops before any disk check, plan, prompt, download or file write'
    }
    Reset-Spark
    $script:s.Existing = New-Foreign 'Disabled' 'other-engine'
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -ceq ("The Windows model task ODSLlamaServerRuntime-S-1-5-21-1 is disabled, but its server is still running (port 8080 is used by other-engine). " +
        'Close it from its own app, or restart Windows (the disabled task will not start it again), then rerun setup. Nothing was changed.') -and $script:calls.Count -eq 2) 'a disabled foreign task whose server still listens stops setup too (M21b)'
    Reset-Spark
    $script:s.Existing = New-Foreign 'Disabled'
    Add-Answers @($true)
    Check ((Invoke-Setup) -eq 0 -and $script:cutover.Existing.Kind -ceq 'foreign' -and $script:cutover.KeepPort -eq $false) 'a disabled, stopped foreign task is handed to the cutover, which replaces it (after saving it)'

    # --- The generic checks come before the RTX Spark's plan -------------------------
    Reset-Spark
    $script:s.WslPresent = $false; $script:s.WslReady = $false; $script:s.FreeGB = 12
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -match '40 GB' -and -not $script:calls.Contains('layout') -and $script:prompts.Count -eq 0) 'too little disk for WSL stops in the generic checks, before the memory layout or any prompt'

    # --- Disk too small for every GPU model (M7) and for the top rung (M6) -----------
    foreach ($answer in @($false, $null)) {
        Reset-Spark -Change @{ FreeGiB = [pscustomobject]@{ 'C:' = 8 } }
        $options = @{}
        if ($null -eq $answer) { $options.NonInteractive = $true } else { Add-Answers @($answer) }
        $message = Get-Failure { $null = Invoke-Setup $options }
        Check ($message -match '^There is not enough free disk space for a GPU model, so setup stopped; nothing was downloaded or changed\.' -and
            $script:prompts[0] -match '^C: has 8\.6 GB free, but the smallest GPU model plan for this PC needs about 23\.1 GB \(model, test model, llama\.cpp and room for Ubuntu and Docker images\)\. Continue with the model on the CPU inside Ubuntu \(slower\)\?' -and
            -not $script:calls.Contains('runtime-install') -and -not ($script:calls -like 'download:*')) "a disk too small for any GPU model asks to use the CPU (M7); $(if ($null -eq $answer) { 'non-interactive setup' } else { 'a no' }) stops with nothing downloaded"
    }
    Reset-Spark -Change @{ FreeGiB = [pscustomobject]@{ 'C:' = 8 } }
    Add-Answers @($true)
    Check ((Invoke-Setup) -eq 0 -and $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and $script:linux.Environment.Count -eq 1 -and
        -not ((@($script:linux.Arguments) -join ' ').Contains('--native-llm')) -and -not $script:calls.Contains('runtime-install') -and
        -not $script:calls.Contains('cutover') -and -not $script:calls.Contains('nvidia-gate')) 'accepting the CPU route forwards GPU_BACKEND=cpu (so Ubuntu never claims the GPU) and downloads nothing on Windows'
    Reset-Spark -Change @{}
    Add-Answers @($true)
    $null = Invoke-Setup
    Check ((Get-Output).Contains("C: has 26.9 GB free. Qwen 3.6 35B-A3B needs about 45.2 GB (download 23.7 GB, and room for Ubuntu and Docker images). ODS uses Qwen 3.5 4B (64K context) now. Free space on C: and rerun setup to get Qwen 3.6 35B-A3B. $helpLine") -and
        $script:cutover.Plan.GgufFile -ceq 'Qwen3.5-4B-Q4_K_M.gguf' -and $script:cutover.Plan.ContextSize -eq 65536) 'the measured Spark (26.9 GB free) says what the 35B-A3B rung needs (M6) and installs the 4B fallback'

    # --- No change could make a GPU model fit: the CPU route, nothing downloaded -----
    Reset-Spark -Change @{ FreeGiB = [pscustomobject]@{ 'C:' = 300 }; CarveOutMiB = 2000 }
    $script:s.Host = $measuredHost.PSObject.Copy(); $script:s.Host.CarveOutMiB = 2000
    Check ((Invoke-Setup) -eq 0 -and $script:prompts.Count -eq 0 -and (Get-Output).Contains("No GPU model proven on this PC fits its memory (the GPU's share or what Windows can lend), so the model runs on the CPU inside Ubuntu (slower). $helpLine") -and
        $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and -not $script:calls.Contains('runtime-install')) 'a GPU share too small for every proven model goes to the CPU without a download or a prompt'

    # --- Declined or non-interactive runtime download: the CPU route ----------------
    foreach ($options in @(@{}, @{ NonInteractive = $true })) {
        Reset-Spark
        if (-not $options.NonInteractive) { Add-Answers @($false) }
        Check ((Invoke-Setup $options) -eq 0 -and $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and -not $script:calls.Contains('runtime-install') -and
            (Get-Output).Contains("Continuing without the GPU: the model runs on the CPU inside Ubuntu (slower). Rerun setup to use the GPU. $helpLine")) "$(if ($options.NonInteractive) { 'non-interactive setup' } else { 'a declined download' }) keeps the RTX Spark on the CPU route"
    }

    # --- Failure classes on a fresh install --------------------------------------------
    Reset-Spark
    $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll'))
    Add-Answers @($true, $true)
    Check ((Invoke-Setup) -eq 0 -and $script:prompts[1] -match '^llama\.cpp could not start because a Windows component is missing \(0xC0000135\), most likely the Microsoft Visual C\+\+ 2015-2022 Redistributable for Arm64\. Install it now\?' -and
        $script:prompts[1].Contains('https://aka.ms/vs/17/release/vc_redist.arm64.exe') -and $script:prompts[1].Contains($helpLine) -and
        $script:wingetCalls.Count -eq 1 -and $script:wingetCalls[0] -ceq 'install --id Microsoft.VCRedist.2015+.arm64 --exact --source winget --accept-package-agreements --accept-source-agreements' -and
        @($script:calls | Where-Object { $_ -eq 'qualify-build' }).Count -eq 2 -and $script:calls.Contains('cutover')) 'a missing Arm64 Visual C++ runtime is offered (M15), installed with winget after a yes, and the check runs once more and passes'
    Reset-Spark
    $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll')); $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll'))
    Add-Answers @($true, $true)
    Check ((Invoke-Setup) -eq 0 -and @($script:calls | Where-Object { $_ -eq 'qualify-build' }).Count -eq 2 -and $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and
        (Get-Output) -match 'exit code -1073741515\. A DLL it needs is missing \(0xC0000135\).+Setup continues with the model on the CPU inside Ubuntu \(slower\)\.') 'one retry only: a runtime still missing after the install gives the CPU route with the cause'
    Reset-Spark
    $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll'))
    Add-Answers @($true, $false)
    Check ((Invoke-Setup) -eq 0 -and $script:wingetCalls.Count -eq 0 -and @($script:calls | Where-Object { $_ -eq 'qualify-build' }).Count -eq 1 -and
        $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu') 'a declined Visual C++ install gives the CPU route without a retry'
    Reset-Spark
    $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll'))
    $script:s.WingetCode = 1
    Add-Answers @($true, $true)
    Check ((Invoke-Setup) -eq 0 -and $script:wingetCalls.Count -eq 1 -and @($script:calls | Where-Object { $_ -eq 'qualify-build' }).Count -eq 1 -and
        (Get-Output) -match 'winget could not install the Visual C\+\+ runtime for Arm64 \(exit 1\)\. Install it from https://aka\.ms/vs/17/release/vc_redist\.arm64\.exe') 'a failed winget install says how to install it by hand and gives the CPU route'
    # The fixture's winget exit code is test data; CI's PowerShell step exits with LASTEXITCODE.
    $global:LASTEXITCODE = 0
    $policy = New-ODSNativeLlamaFailure ('llama-server.exe could not start: blocked. ' + (Get-ODSNativeLlamaStartHint 4551)) 'policy'
    foreach ($case in @(
        @{ Name = 'policy'; Build = $policy; Pattern = 'Windows blocked llama\.cpp \(Smart App Control or an application control policy\): llama-server\.exe could not start: blocked\. Smart App Control or Windows Defender Application Control blocks it\. ODS does not change that setting\. Setup continues with the model on the CPU' },
        @{ Name = 'start'; Build = (New-ODSNativeLlamaFailure 'llama-server.exe could not start: gone. The file is gone, often removed by antivirus software after download.' 'start'); Pattern = 'removed by antivirus software after download\. Setup continues with the model on the CPU' },
        @{ Name = 'timeout'; Build = (New-ODSNativeLlamaFailure 'llama-server.exe --version did not finish within 60 seconds.' 'timeout'); Pattern = 'did not finish within 60 seconds\. Setup continues with the model on the CPU' },
        @{ Name = 'exit'; Build = (New-RunFailure -1073741819 'exit'); Pattern = '0xC0000005\), usually inside the graphics driver\. Update the NVIDIA graphics driver.+Setup continues with the model on the CPU' },
        @{ Name = 'build'; Build = (New-ODSNativeLlamaFailure 'llama-server.exe reports build 9014, not the pinned b11429.' 'build'); Pattern = 'reports build 9014, not the pinned b11429\. Setup continues with the model on the CPU' })) {
        Reset-Spark
        $script:s.Builds.Enqueue($case.Build)
        Add-Answers @($true)
        Check ((Invoke-Setup) -eq 0 -and $script:prompts.Count -eq 1 -and (Get-Output) -match $case.Pattern -and (Get-Output).Contains($helpLine) -and
            $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and -not ($script:calls -like 'download:*') -and -not $script:calls.Contains('cutover')) "class $($case.Name): the cause, then the CPU route (M23 for policy); no test model was downloaded"
    }
    Reset-Spark
    $script:s.Builds.Enqueue((New-Build $false))
    Add-Answers @($true)
    Check ((Invoke-Setup) -eq 0 -and (Get-Output) -match 'llama\.cpp found no NVIDIA CUDA GPU \(it lists: CUDA0 NVIDIA RTX Spark N1X \(6144-core Blackwell RTX GPU\)\)|llama\.cpp found no NVIDIA CUDA GPU' -and
        -not ($script:calls -like 'download:*') -and -not ($script:calls -like 'gpu-check:*') -and $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu') 'no CUDA device (M13): the CPU route, before the test model is downloaded'
    foreach ($case in @(@{ Name = 'build (M11)'; Check = (New-Check $badArchs); Pattern = 'has CUDA kernels for 500,610,700,750,800,860,890,1200, not for this GPU \(compute capability 12\.1\)' },
            @{ Name = 'generation (M12)'; Check = (New-Check $badAnswer); Pattern = 'answered "\?+" to a test question \(expected 4\)' })) {
        Reset-Spark
        $script:s.Checks.Enqueue($case.Check)
        Add-Answers @($true)
        Check ((Invoke-Setup) -eq 0 -and (Get-Output) -match ($case.Pattern + '.+Setup continues with the model on the CPU inside Ubuntu \(slower\)\.') -and
            $script:calls.Contains("download:$($testArtifact.File)") -and -not (Test-Path -LiteralPath (Join-Path $modelsDir $testArtifact.File)) -and
            $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu') "a failed GPU check, $($case.Name): the CPU route, and the test model this run downloaded is deleted"
    }
    Reset-Spark
    Add-StoreFile $testArtifact.File
    $script:s.Checks.Enqueue((New-Check $badAnswer))
    Add-Answers @($true)
    $null = Invoke-Setup
    Check ($script:prompts[0] -match 'and check it on your GPU with the test model already downloaded\?' -and (Test-Path -LiteralPath (Join-Path $modelsDir $testArtifact.File)) -and
        $script:calls.Contains("verify:$($testArtifact.File)")) 'a test model that was in the store before stays after a failed check (and is only verified)'
    Reset-Spark
    $script:s.Host = $measuredHost.PSObject.Copy(); $script:s.Host.DriverVersion = '600.10'
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -match '^The NVIDIA driver \(600\.10\) is older than 616\.41, which the llama\.cpp CUDA 13\.4 build needs\..+Nothing was downloaded or changed\.$' -and
        $script:prompts.Count -eq 0 -and -not $script:calls.Contains('runtime-install')) 'a driver older than the CUDA build needs stops setup before any prompt or download (M14)'

    # --- A rerun with ODS's runtime: a failure changes nothing --------------------
    Reset-Spark
    Set-RuntimeInstalled
    Add-StoreFile $topFile
    $script:s.Existing = New-Existing
    $script:s.Checks.Enqueue((New-Check $badAnswer))
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -match 'answered "\?+" to a test question \(expected 4\).+Your current model was not changed\.$' -and $script:prompts.Count -eq 0 -and
        -not $script:calls.Contains('cutover') -and -not $script:calls.Contains('linux-installer')) 'a failed check on a rerun stops setup and says the current model was not changed'
    Reset-Spark
    Set-RuntimeInstalled
    $script:s.Existing = New-Existing
    $script:s.Builds.Enqueue((New-RunFailure -1073741515 'dll'))
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -match '0xC0000135.+Your current model was not changed\.$' -and $script:wingetCalls.Count -eq 0 -and $script:prompts.Count -eq 0) 'a rerun never offers the Visual C++ install: it stops with the cause'

    # --- The deferred check: the current model holds the GPU ------------------------
    Reset-Spark
    Set-RuntimeInstalled
    Add-StoreFile $topFile
    $script:s.Existing = New-Existing
    $script:s.Gpu = [pscustomobject]@{ Name = $measuredHost.GpuName; TotalMiB = 98240; UsedMiB = 96000; DriverVersion = '616.62' }
    $script:s.Commit = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 60 }
    $null = Invoke-Setup
    Check ((Get-Index 'gpu-check:CUDA0') -gt (Get-Index 'cutover') -and $script:cutover.BeforeStart -and $script:cutover.KeepPort -and
        (Get-Output) -match 'llama\.cpp is checked on the GPU once your current model stops for the switch' -and
        (Test-Path -LiteralPath (Join-Path $runtimeDir 'qualification.json'))) 'with under 3 GiB of GPU memory free, a rerun runs the check inside the cutover, after the current model stopped, and caches a pass'
    Reset-Spark
    Set-RuntimeInstalled
    Add-StoreFile $topFile
    $script:s.Existing = New-Existing
    $script:s.Gpu = [pscustomobject]@{ Name = $measuredHost.GpuName; TotalMiB = 98240; UsedMiB = 20000; DriverVersion = '616.62' }
    $script:s.Commit = [pscustomobject]@{ VisibleGiB = 30.49; CommitLimitGiB = 126.49; CommittedGiB = 124.5 }
    $script:s.Checks.Enqueue((New-Check $badArchs))
    $failure = $null
    try { $null = Invoke-Setup } catch { $failure = $_.Exception }
    Check ($failure -and $failure.Data['gpuCheck'] -and $failure.Message -match 'not for this GPU \(compute capability 12\.1\)' -and $script:calls.Contains('cutover') -and
        -not $script:calls.Contains('linux-installer')) 'with under 3 GiB of commit free the check is deferred too; its failure reaches the cutover marked, so the cutover restores the current model'

    # --- The memory offers and the one shared restart ---------------------------------
    Reset-Spark -Layout 'memory-96-nopf.json'
    $script:s.DockerInstalled = $false
    Add-Answers @($true, $true, $true)
    $code = Invoke-Setup
    $output = Get-Output
    Check ($code -eq 3010 -and $script:calls.Contains('pagefile:32') -and (Get-Index 'docker-install') -ge 0 -and (Get-Index 'docker-install') -lt (Get-Index 'resume') -and
        @($script:calls | Where-Object { $_ -eq 'resume' }).Count -eq 1 -and -not $script:calls.Contains('linux-installer') -and -not $script:calls.Contains('cutover')) 'accepting the page file (M2) with WSL ready: Docker Desktop installs first, then one restart, before anything else changes'
    Check ($output.Contains('A 32 GB page file was set for the GPU model; Windows applies it after a restart. Use Restart, not Shut down (with Fast Startup, Shut down may not apply it).') -and
        $output -match 'ODS itself is not installed yet\. Already done: llama\.cpp b11429 for NVIDIA GPUs downloaded to .+; test model Qwen3\.5-2B-Q4_K_M\.gguf downloaded; page file on C: set to 32 GB\.' -and
        -not $output.Contains('ODS has not been installed yet.')) 'the restart notice says why, to use Restart, and what setup already did'
    Reset-Spark -Layout 'memory-96-nopf.json'
    $script:s.WslPresent = $false; $script:s.WslReady = $false
    Add-Answers @($true, $true, $true)
    $code = Invoke-Setup
    Check ($code -eq 3010 -and $script:calls.Contains('wsl-features') -and @($script:calls | Where-Object { $_ -eq 'resume' }).Count -eq 1 -and
        (Get-Output).Contains('WSL was prepared and Windows needs a restart. A 32 GB page file was set for the GPU model; Windows applies it after a restart.')) 'WSL''s own restart also applies the page file: one restart, both reasons'
    Reset-Spark -Layout 'memory-96-nopf.json'
    $script:s.WslPresent = $false; $script:s.WslReady = $false
    Add-Answers @($true, $true, $false)
    Check ((Invoke-Setup) -eq 1 -and -not $script:calls.Contains('wsl-features') -and
        (Get-Output).Contains('The page file change waits for a restart. Use Restart, not Shut down (with Fast Startup, Shut down may not apply it). It applies at your next restart.')) 'declining WSL with a page-file change pending stops with the restart reminder (M24)'
    Reset-Spark -Layout 'memory-pending.json' -Change @{ FreeGiB = [pscustomobject]@{ 'C:' = 300 } }
    Add-Answers @($true)
    $code = Invoke-Setup
    Check ($code -eq 3010 -and -not ($script:calls -like 'pagefile:*') -and $script:prompts.Count -eq 1 -and
        (Get-Output).Contains('The page file change waits for a restart. Use Restart, not Shut down (with Fast Startup, Shut down may not apply it). Setup continues by itself after you sign in.')) 'a page-file change still waiting (Shut down was used) is never offered again: no UAC, a restart request (M24)'
    Reset-Spark -Layout 'memory-96-nopf.json'
    Add-Answers @($true, $false)
    Check ((Invoke-Setup) -eq 0 -and -not ($script:calls -like 'pagefile:*') -and -not $script:calls.Contains('resume') -and
        $script:linux.Environment['GPU_BACKEND'] -ceq 'cpu' -and -not (Test-Path -LiteralPath (Join-Path $modelsDir $testArtifact.File)) -and
        (Get-Output) -match 'Page file not changed\. No GPU model fits the memory Windows has now') 'a declined page file with nothing fitting now: the CPU route (M3), and the test model is removed'
    Reset-Spark -Layout 'memory-factory-64.json'
    Add-Answers @($true, $false, $false)
    Check ((Invoke-Setup) -eq 0 -and $script:cutover.Plan.GgufFile -ceq 'Qwen3.5-9B-Q4_K_M.gguf' -and
        (Get-Output) -match 'ODS uses Qwen 3\.5 9B \(64K context\) .+the largest model proven on this PC that fits') 'a factory Spark that declines both offers gets the 9B fallback that fits (M3)'

    # --- Migrating an older container install (M22) -----------------------------------
    Reset-Spark
    Add-Answers @($true)
    $script:s.EnvGrep = [pscustomobject]@{ Code = 0; Output = "GPU_BACKEND=`"nvidia`"`nNATIVE_LLM_BASE_URL=" }
    $script:s.ContainerIds = @('0123456789ab')
    Check ((Invoke-Setup) -eq 0 -and (Get-Index 'wsl:--distribution Ubuntu-24.04 --exec docker stop 0123456789ab') -lt (Get-Index 'cutover') -and
        (Get-Output).Contains('This PC ran the model in a container in Ubuntu, which cannot use this GPU correctly. ODS now stops that container and runs the model with llama.cpp on Windows; chat is back when setup finishes. Model files in Ubuntu stay until you delete them on the Models page.')) 'an older install''s ODS model container (compose project ods, service llama-server) is stopped before the cutover (M22)'
    Reset-Spark
    Add-Answers @($true)
    $script:s.EnvGrep = [pscustomobject]@{ Code = 0; Output = "GPU_BACKEND=nvidia`nNATIVE_LLM_BASE_URL=http://localhost:8080" }
    $script:s.ContainerIds = @('0123456789ab')
    $null = Invoke-Setup
    Check (-not ($script:calls -like '*docker ps*') -and -not ($script:calls -like '*docker stop*')) 'an install already on the Windows server is not migrated'
    foreach ($grep in @([pscustomobject]@{ Code = 0; Output = 'GPU_BACKEND=cpu' }, [pscustomobject]@{ Code = 1; Output = '' }, [pscustomobject]@{ Code = 2; Output = '' })) {
        Reset-Spark
        Add-Answers @($true)
        $script:s.EnvGrep = $grep
        $null = Invoke-Setup
        Check (-not ($script:calls -like '*docker ps*')) "nothing to migrate when .env has no NVIDIA container route (grep exit $($grep.Code))"
    }
    Reset-Spark
    Add-Answers @($true)
    $script:s.EnvGrep = [pscustomobject]@{ Code = 0; Output = 'GPU_BACKEND=nvidia' }
    $script:s.ContainerIds = @('0123456789ab', 'cdef01234567')
    $script:s.CutoverFailure = 'The llama.cpp runtime did not start: fixture. Nothing was left running.'
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -ceq "The llama.cpp runtime did not start: fixture. Nothing was left running. ODS's model container in Ubuntu was started again." -and
        $script:calls.Contains('wsl:--distribution Ubuntu-24.04 --exec docker start 0123456789ab cdef01234567')) 'a failed cutover starts the stopped container again and says so'
    Reset-Spark
    Add-Answers @($true)
    $script:s.EnvGrep = [pscustomobject]@{ Code = 0; Output = 'GPU_BACKEND=nvidia' }
    $script:s.ContainerIds = @('0123456789ab')
    $script:s.DockerStopCode = 125
    $message = Get-Failure { $null = Invoke-Setup }
    Check ($message -match "^ODS could not stop its model container in Ubuntu-24\.04 \(docker stop exit 125\).+docker stop ods-llama-server, then rerun setup\.$" -and
        -not $script:calls.Contains('cutover')) 'a container that will not stop stops setup before the cutover, with the command to run'

    # --- The owner's Models-page choice ---------------------------------------------
    Reset-Spark
    Set-RuntimeInstalled
    Add-StoreFile 'Qwen3.5-9B-Q4_K_M.gguf'
    $script:s.Existing = New-Existing 'Qwen3.5-9B-Q4_K_M.gguf' 65536 18080
    $null = Invoke-Setup
    Check ($script:cutover.Plan.GgufFile -ceq 'Qwen3.5-9B-Q4_K_M.gguf' -and $script:cutover.Plan.Port -eq 18080 -and $script:cutover.KeepPort -and
        -not ($script:calls -like "download:$topFile") -and $script:cutover.MemoryPlan.GgufFile -ceq 'Qwen3.5-9B-Q4_K_M.gguf' -and
        $script:cutover.MemoryPlan.DeviceNeedGiB -eq 7.77 -and (@($script:linux.Arguments) -join ' ') -match '--native-llm-model Qwen3\.5-9B-Q4_K_M\.gguf --native-llm-context-size 65536') 'a rerun keeps the owner''s model, context and port when its files are there and it fits; nothing is downloaded'
    Reset-Spark
    Set-RuntimeInstalled
    Add-StoreFile 'Big-00001-of-00002.gguf'
    $script:s.Existing = New-Existing 'Big-00001-of-00002.gguf' 65536
    $null = Invoke-Setup
    Check ($script:cutover.Plan.GgufFile -ceq $topFile -and (Get-Output).Contains("Your selected model Big-00001-of-00002.gguf is missing Big-00002-of-00002.gguf in the Windows model store; using the recommended $topFile.")) 'a split model missing a part is not carried; the plan''s model is downloaded instead'
    Reset-Spark -Layout 'memory-96-nopf.json' -Change @{ FreeGiB = [pscustomobject]@{ 'C:' = 300 }; PageFileConfiguredGiB = 16; PageFileActiveGiB = 16 }
    Set-RuntimeInstalled
    Add-StoreFile 'Qwen3.5-9B-Q4_K_M.gguf'
    Add-StoreFile $topFile
    $script:s.Existing = New-Existing $topFile 131072
    # The page-file offer for the larger model is declined.
    Add-Answers @($false)
    $null = Invoke-Setup
    Check ($script:cutover.Plan.GgufFile -ceq 'Qwen3.5-9B-Q4_K_M.gguf' -and (Get-Output) -match "Your selected model $([regex]::Escape($topFile)) needs about 25\.8 GiB, but Windows can lend about 13\.\d GiB now; using the recommended Qwen3\.5-9B-Q4_K_M\.gguf\.") 'a selection that no longer fits this memory is replaced by the plan, with the reason'

    # --- -Tier, a failed Linux step, and the CPU route's notice -----------------------
    Reset-Spark
    Add-Answers @($true)
    $null = Invoke-Setup @{ Tier = '3' }
    Check (@($script:linux.Arguments | Where-Object { $_ -eq '--tier' }).Count -eq 1 -and (@($script:linux.Arguments) -join ' ') -match '--tier 3 ') 'an explicit -Tier is kept and --tier 4 is not added'
    Reset-Spark
    Add-Answers @($true)
    $script:s.LinuxCode = 9
    Check ((Invoke-Setup) -eq 9 -and (Get-Output) -match 'The GPU model server on Windows is ready, but the Linux installer did not finish') 'a failed Linux step after the native cutover says the Windows model server is ready'
    Reset-Spark
    Add-Answers @($false)
    $script:s.LinuxCode = 9
    Check ((Invoke-Setup) -eq 9 -and -not ((Get-Output) -match 'The GPU model server on Windows is ready')) 'the CPU route forwards an environment but never claims a Windows model server'

    # --- Every other PC: the RTX Spark route is never taken ---------------------------
    Reset-Spark
    $script:s.Host = $null
    Check ((Invoke-Setup) -eq 0 -and $script:calls.Contains('nvidia-driver') -and $script:calls.Contains('nvidia-gate') -and -not $script:calls.Contains('layout') -and
        -not $script:calls.Contains('existing:True') -and $script:prompts.Count -eq 0 -and -not ((@($script:linux.Arguments) -join ' ').Contains('--native-llm'))) 'a PC that is not an RTX Spark keeps the NVIDIA container gate and reads nothing of the Spark route'
    Reset-Spark
    $null = Invoke-Setup @{ Cloud = $true }
    Check (-not $script:calls.Contains('spark-host') -and -not $script:calls.Contains('layout') -and -not $script:calls.Contains('nvidia-gate')) 'cloud mode on an RTX Spark runs no model on it: no Spark route'
} finally {
    foreach ($name in $previous.Keys) {
        if ($null -eq $previous[$name]) { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue } else { Set-Item -Path "Env:\$name" -Value $previous[$name] }
    }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
$global:LASTEXITCODE = 0
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows Portal RTX Spark route contracts."
