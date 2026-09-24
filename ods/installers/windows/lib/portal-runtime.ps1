. (Join-Path $PSScriptRoot 'backend-contract.ps1')
. (Join-Path $PSScriptRoot 'portal-model-store.ps1')

function Initialize-ODSPortalWindowsRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$SourceRoot,
        [Parameter(Mandatory=$true)][psobject]$Identity,
        [Parameter(Mandatory=$true)][string]$ModelsDirectory,
        [Parameter(Mandatory=$true)][hashtable]$Model,
        [ValidateRange(1,65535)][int]$Port=18080,
        [switch]$DryRun
    )
    $context = [int]$Model.MaxContext
    if ($context -le 0 -or $context -gt 10000000) { throw 'Invalid selected model context.' }
    $paths = Get-ODSPortalModelStorePaths $ModelsDirectory
    if ($DryRun) {
        return [pscustomobject]@{ Prepared=$false; ModelsWindowsPath=$paths.WindowsPath; ModelsWslPath=$paths.WslPath; Port=$Port }
    }
    # The WSL lifecycle directory already provides an owner-only ACL, including
    # the secrets read by the scheduled launcher. Never write a key into a task.
    Initialize-ODSPrivateDirectory $Identity.directory
    $lock = Open-ODSPrivateLock (Join-Path $Identity.directory 'inference.lock')
    try {
        $installed = Install-ODSLemonadeRuntime -RootPath $SourceRoot -WorkDirectory (Join-Path $Identity.directory 'runtime-install')
        $executable = $installed.ExecutablePath
        $allProcesses = @(Get-CimInstance Win32_Process)
        $processes = @($allProcesses | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.Equals($executable, [StringComparison]::OrdinalIgnoreCase)
        })
        # Lemonade 10 delegates its listening socket to a router child. Verify
        # both its executable path and parent, not just the process name.
        $routerPath = Join-Path (Split-Path $executable -Parent) 'lemonade-router.exe'
        $routers = @($allProcesses | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.Equals($routerPath, [StringComparison]::OrdinalIgnoreCase) -and
            $_.ParentProcessId -in @($processes.ProcessId)
        })
        $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Where-Object {
            $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::')
        })
        $owned = @($listeners | Where-Object { $_.OwningProcess -in (@($processes.ProcessId) + @($routers.ProcessId)) })
        if ($listeners.Count -gt 0 -and $owned.Count -ne $listeners.Count) {
            throw "Port $Port belongs to another process. Select a free inference port."
        }
        if ($processes.Count -gt 0 -and $owned.Count -eq 0) {
            throw 'Lemonade is already running on another port. Stop or reconcile that runtime before starting the ODS-owned instance.'
        }
        if (@($owned | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
            throw 'The existing Lemonade listener is not loopback-only. Reconcile its binding before adoption.'
        }
        $store = Initialize-ODSPortalModelStore -WindowsPath $paths.WindowsPath -Model $Model
        $envPath = Join-Path $Identity.directory 'inference.env'
        $apiKey = Get-ODSLemonadeAdminApiKey -EnvPath $envPath
        if (-not $apiKey -and $Identity.id) {
            $legacyDirectory = Join-Path $env:LOCALAPPDATA "ODS\wsl\$($Identity.id)"
            $legacyEnvPath = Join-Path $legacyDirectory 'inference.env'
            if ($legacyDirectory -ine $Identity.directory -and (Test-Path -LiteralPath $legacyEnvPath)) {
                # Keep authentication to a running modern server when moving
                # from the former (potentially virtualized) task directory.
                Assert-ODSPrivatePath $legacyDirectory -Directory
                Assert-ODSPrivatePath $legacyEnvPath
                $apiKey = Get-ODSLemonadeAdminApiKey -EnvPath $legacyEnvPath
            }
        }
        if (-not $apiKey) {
            $random = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($random) } finally { $rng.Dispose() }
            $apiKey = -join ($random | ForEach-Object { $_.ToString('x2') })
        }
        [IO.File]::WriteAllText($envPath, "LITELLM_LEMONADE_API_KEY=$apiKey`n", [Text.UTF8Encoding]::new($false))
        $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $executable -Port $Port `
            -ModelsDir $paths.WindowsPath -ContextSize $context -AdminApiKey $apiKey
        $headers = @{ Authorization="Bearer $apiKey" }
        if ($owned.Count -gt 0 -and -not $contract.Modern) {
            # Do not change an unrelated legacy instance's model directory.
            $expected = '--extra-models-dir "' + $paths.WindowsPath + '"'
            $expectedUnquoted = '--extra-models-dir ' + $paths.WindowsPath
            $serverId = $owned[0].OwningProcess
            $router = $routers | Where-Object { $_.ProcessId -eq $serverId } | Select-Object -First 1
            if ($router) { $serverId = $router.ParentProcessId }
            $command = [string]($processes | Where-Object { $_.ProcessId -eq $serverId } | Select-Object -First 1).CommandLine
            if (-not $command.Contains($expected) -and -not ($command.EndsWith($expectedUnquoted) -or $command.Contains($expectedUnquoted + ' '))) {
                throw 'Existing Lemonade uses another model directory; it was not reconfigured.'
            }
        }
        if ($owned.Count -gt 0 -and $contract.Modern) {
            # A modern pre-existing server may have another owner's admin key.
            # Verify access and model storage before creating an ODS task.
            $existingConfig = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/internal/config" -Headers $headers -TimeoutSec 10
            if ([IO.Path]::GetFullPath([string]$existingConfig.extra_models_dir) -ine $paths.WindowsPath) {
                throw 'Existing Lemonade uses another model directory; it was not reconfigured.'
            }
        }
        $taskName = $Identity.taskName + '-Inference'
        $log = Join-Path $Identity.directory 'inference-launch.log'
        $action = New-ODSLemonadeScheduledTaskAction -Contract $contract -EnvPath $envPath -DiagnosticLogPath $log
        $principal = New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Resolve-ODSInteractiveScheduledTaskUser)
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $trigger `
            -Settings $settings -Force | Out-Null
        if ($owned.Count -eq 0) { Start-ScheduledTask -TaskName $taskName }
        $healthy = $false
        for ($attempt=0; $attempt -lt 30; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -Headers $headers -TimeoutSec 3
                if ($health.status -in @('ok','success')) { $healthy=$true; break }
            } catch { }
            Start-Sleep -Seconds 2
        }
        if (-not $healthy) { throw "The ODS inference task did not become healthy. Diagnostic log: $log" }
        if ($contract.RequiresRuntimeConfiguration) {
            $null = Set-ODSLemonadeModernRuntimeConfig -Port $Port -ModelsDir $paths.WindowsPath -AdminApiKey $apiKey -ContextSize $context
        }
        $modelId = Resolve-ODSLemonadeModelId -Port $Port -GgufFile $store.Filename -VersionOverride ([string]$contract.Version)
        Set-ODSLemonadeLoadedModel -Port $Port -ModelId $modelId -ContextSize $context -ApiKey $apiKey
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -Headers $headers -TimeoutSec 10
        $loaded = @($health.all_models_loaded | Where-Object { $_.model_name -ceq $modelId })
        if ($loaded.Count -ne 1 -or $loaded[0].device -ne 'gpu' -or $loaded[0].recipe_options.llamacpp_backend -ne 'vulkan') {
            throw 'The runtime did not prove Vulkan GPU inference for the selected model.'
        }
        if ([long]$loaded[0].recipe_options.ctx_size -ne $context) { throw 'The loaded context differs from the selected context.' }
        $body = @{ model=$modelId; messages=@(@{role='user';content='Reply with OK.'}); max_tokens=32; stream=$false; chat_template_kwargs=@{enable_thinking=$false} } | ConvertTo-Json -Depth 5
        $completion = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/v1/chat/completions" `
            -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 120
        if ([string]::IsNullOrWhiteSpace([string]$completion.choices[0].message.content)) {
            throw 'The runtime did not produce a usable completion.'
        }
        if ([string]$completion.model -cnotin @($modelId, $store.Filename, [IO.Path]::GetFileNameWithoutExtension($store.Filename))) {
            throw 'The completion came from a different model.'
        }
        return [pscustomobject]@{
            Prepared=$true; ModelsWindowsPath=$paths.WindowsPath; ModelsWslPath=$paths.WslPath
            Port=$Port; ModelId=$modelId; Context=$context; ApiKey=$apiKey; TaskName=$taskName
        }
    } finally { $lock.Dispose() }
}
