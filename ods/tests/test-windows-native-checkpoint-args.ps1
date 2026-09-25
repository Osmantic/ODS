$ErrorActionPreference = "Stop"

# Contract: native Windows llama-server launches pass
# LLAMA_ARG_CHECKPOINT_EVERY_NT as --checkpoint-every-n-tokens only when the
# value is valid and the selected binary's --help lists the flag. llama.cpp
# removed the flag in b9310, and llama-server exits on an unknown flag. The
# rules mirror installers/macos/lib/native-checkpoint-args.py.

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers\windows\lib\native-llama-args.ps1")

$onWindows = ($env:OS -eq "Windows_NT")
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-native-checkpoint-$([Guid]::NewGuid().ToString('N'))"

function New-FakeLlamaServer {
    param([string]$Name, [string[]]$HelpLines, [int]$ExitCode = 0)
    if ($onWindows) {
        $path = Join-Path $testRoot "$Name.cmd"
        $body = @("@echo off") + ($HelpLines | ForEach-Object { "echo $_" }) + @("exit /b $ExitCode")
        [IO.File]::WriteAllText($path, (($body -join "`r`n") + "`r`n"))
    } else {
        $path = Join-Path $testRoot $Name
        $body = @("#!/bin/sh") + ($HelpLines | ForEach-Object { "printf '%s\n' '$_'" }) + @("exit $ExitCode")
        [IO.File]::WriteAllText($path, (($body -join "`n") + "`n"))
        & chmod +x $path
    }
    return $path
}

function Assert-Args {
    param($Result, [string[]]$Expected, [bool]$Warned, [string]$Case)
    $actual = @($Result.Arguments)
    if (($actual -join " ") -ne ($Expected -join " ")) {
        throw "${Case}: expected [$($Expected -join ' ')], got [$($actual -join ' ')]"
    }
    if ([bool]$Result.Warning -ne $Warned) {
        throw "${Case}: warning expected=$Warned, got '$($Result.Warning)'"
    }
}

try {
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $b9014 = New-FakeLlamaServer -Name "b9014" -HelpLines @(
        "-cpent, --checkpoint-every-n-tokens N   create a checkpoint every n tokens",
        "-ctxcp, --ctx-checkpoints N")
    $b11146 = New-FakeLlamaServer -Name "b11146" -HelpLines @(
        "-cms,   --checkpoint-min-step N   minimum spacing between checkpoints",
        "-ctxcp, --ctx-checkpoints N")
    $lookalike = New-FakeLlamaServer -Name "lookalike" -HelpLines @("--checkpoint-every-n-tokens-extra N")
    $failing = New-FakeLlamaServer -Name "failing" -HelpLines @("--checkpoint-every-n-tokens N") -ExitCode 1

    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b9014 -Value "") @() $false "unset"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b9014 -Value "1024") @("--checkpoint-every-n-tokens", "1024") $false "b9014 1024"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b9014 -Value "-1") @("--checkpoint-every-n-tokens", "-1") $false "b9014 -1"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b9014 -Value '"2048"') @("--checkpoint-every-n-tokens", "2048") $false "quoted"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b11146 -Value "1024") @() $true "b11146 has no flag"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $lookalike -Value "1024") @() $true "longer flag name"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $failing -Value "1024") @() $true "help exits non-zero"
    Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable (Join-Path $testRoot "missing") -Value "1024") @() $true "missing binary"
    foreach ($bad in @("0", "-2", "262145", "abc", "1e3", "1024 --metrics")) {
        Assert-Args (Get-ODSNativeCheckpointIntervalArgs -Executable $b9014 -Value $bad) @() $true "invalid '$bad'"
    }

    # LLAMA_REASONING: --reasoning where the binary has it (b9014 defaults it
    # to auto), else the caller's --reasoning-format mapping (b8248).
    # (No "|" in the fake help: cmd.exe would treat it as a pipe.)
    $withReasoning = New-FakeLlamaServer -Name "reasoning" -HelpLines @(
        "-rea, --reasoning [on,off,auto]   use reasoning/thinking in the chat",
        "--reasoning-format FORMAT",
        "--reasoning-budget N")
    $b8248Reasoning = New-FakeLlamaServer -Name "b8248-reasoning" -HelpLines @(
        "--reasoning-format FORMAT",
        "--reasoning-budget N   -1 for unrestricted thinking budget, or 0 to disable thinking")
    $formatOnly = New-FakeLlamaServer -Name "format-only" -HelpLines @(
        "--reasoning-format FORMAT")
    $removed = New-FakeLlamaServer -Name "removed" -HelpLines @(
        "--reasoning   this flag has been removed")
    function Assert-Reasoning {
        param([string[]]$Actual, [string[]]$Expected, [string]$Case)
        if ((@($Actual) -join " ") -ne ($Expected -join " ")) {
            throw "${Case}: expected [$($Expected -join ' ')], got [$(@($Actual) -join ' ')]"
        }
    }
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $withReasoning -Mode "off" -FallbackFormat "none") @("--reasoning", "off") "b9014 off"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $withReasoning -Mode "" -FallbackFormat "none") @("--reasoning", "off") "b9014 default"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $withReasoning -Mode "auto" -FallbackFormat "auto") @("--reasoning", "auto") "b9014 auto"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $withReasoning -Mode "deepseek" -FallbackFormat "deepseek") @("--reasoning-format", "deepseek") "format name"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $withReasoning -Mode "OFF" -FallbackFormat "OFF") @("--reasoning-format", "OFF") "case-sensitive mode"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $b8248Reasoning -Mode "off" -FallbackFormat "none") @("--reasoning-format", "none", "--reasoning-budget", "0") "b8248 off"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $b8248Reasoning -Mode "" -FallbackFormat "none") @("--reasoning-format", "none", "--reasoning-budget", "0") "b8248 default"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $b8248Reasoning -Mode "on" -FallbackFormat "deepseek") @("--reasoning-format", "deepseek") "b8248 on"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $formatOnly -Mode "off" -FallbackFormat "none") @("--reasoning-format", "none") "no budget flag"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $removed -Mode "off" -FallbackFormat "none") @("--reasoning-format", "none") "removed flag"
    Assert-Reasoning (Get-ODSNativeReasoningArgs -Executable $failing -Mode "off" -FallbackFormat "none") @("--reasoning-format", "none") "help exits non-zero"

    # Every Windows launcher goes through the probes.
    foreach ($relative in @("installers\windows\install-windows.ps1", "installers\windows\ods.ps1")) {
        $text = Get-Content -LiteralPath (Join-Path $root $relative) -Raw
        if ($text -notmatch 'native-llama-args\.ps1') { throw "$relative does not load native-llama-args.ps1" }
        if ($text -notmatch 'Get-ODSNativeCheckpointIntervalArgs') { throw "$relative does not qualify the checkpoint interval" }
        if ($text -match '"--checkpoint-every-n-tokens",\s*\$') { throw "$relative passes --checkpoint-every-n-tokens without the probe" }
        if ($text -notmatch 'Get-ODSNativeReasoningArgs') { throw "$relative does not qualify the reasoning flags" }
    }
    Write-Output "Windows native checkpoint interval and reasoning contract OK"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
