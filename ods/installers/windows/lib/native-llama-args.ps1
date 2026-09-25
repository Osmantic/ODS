# ============================================================================
# ODS Windows Installer -- native llama-server argument qualification
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Spell llama-server flags for the installed binary, which can be
#          older or newer than the ODS pin (existing installs keep theirs).
#
# llama-server exits with "invalid argument" on a flag it does not know, so
# the Windows launchers (install-windows.ps1, ods.ps1) read the binary's own
# --help first, as installers/macos/lib/native-checkpoint-args.py does on
# macOS. scripts/bootstrap-upgrade.sh does the same in Bash for its Windows
# hot-swap, and ods-host-agent.py for dashboard model switches.
#
# - LLAMA_ARG_CHECKPOINT_EVERY_NT becomes --checkpoint-every-n-tokens only for
#   an integer from -1 to 262144 other than 0, on a binary that lists the flag.
#   llama.cpp removed it in b9310 (replaced by --checkpoint-min-step, with
#   different semantics). An unusable value is dropped with a warning: the
#   setting is opt-in, and no shipped profile sets it.
# - LLAMA_REASONING (off, on or auto) becomes --reasoning on binaries that
#   have it (b9014), with llama.cpp's default --reasoning-format, as Docker
#   does through LLAMA_ARG_REASONING. b9014 defaults --reasoning to auto, which
#   turns Qwen3.5 thinking on for every request, and with --reasoning-format
#   none the reasoning comes back inside the reply. Older binaries (b8248) have
#   no --reasoning and keep the caller's --reasoning-format mapping; for off
#   they also get --reasoning-budget 0, the switch that disables thinking
#   there (b8248 accepts only 0 or -1, and defaults to -1, thinking on).
# ============================================================================

$script:ODSLlamaServerHelpCache = @{}

function Get-ODSLlamaServerHelpText {
    <#
    .SYNOPSIS
        The binary's --help output, or $null when it cannot be read.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [int]$TimeoutSeconds = 15
    )

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $Executable
    $cacheKey = "{0}|{1}|{2}" -f $item.FullName, $item.Length, $item.LastWriteTimeUtc.Ticks
    if ($script:ODSLlamaServerHelpCache.ContainsKey($cacheKey)) {
        return $script:ODSLlamaServerHelpCache[$cacheKey]
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Executable
    $psi.Arguments = "--help"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $workingDirectory = Split-Path -Parent $Executable
    if ($workingDirectory) { $psi.WorkingDirectory = $workingDirectory }

    try {
        $process = [System.Diagnostics.Process]::Start($psi)
    } catch {
        return $null
    }
    $helpText = $null
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return $null
        }
        $process.WaitForExit()
        if ($process.ExitCode -eq 0) {
            $helpText = [string]$stdout.Result + [string]$stderr.Result
        }
    } finally {
        $process.Dispose()
    }
    if ($null -eq $helpText -or $helpText.Length -gt 1MB) { return $null }
    $script:ODSLlamaServerHelpCache[$cacheKey] = $helpText
    return $helpText
}

function Test-ODSLlamaServerHelpFlag {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Flag
    )

    $helpText = Get-ODSLlamaServerHelpText -Executable $Executable
    if (-not $helpText) { return $false }
    $pattern = '(?<![\w-])' + [regex]::Escape($Flag) + '(?![\w-])'
    foreach ($line in ($helpText -split "`r?`n")) {
        if ([regex]::IsMatch($line, $pattern) -and $line -notmatch 'has been removed') {
            return $true
        }
    }
    return $false
}

function Get-ODSNativeCheckpointIntervalArgs {
    <#
    .SYNOPSIS
        Qualify LLAMA_ARG_CHECKPOINT_EVERY_NT for a native llama-server.
    .OUTPUTS
        An object with Arguments (empty, or --checkpoint-every-n-tokens and
        the value) and Warning (empty, or why the value was dropped).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [AllowEmptyString()][string]$Value
    )

    $text = ([string]$Value).Trim()
    if ($text.Length -ge 2 -and ($text[0] -eq '"' -or $text[0] -eq "'") -and $text[-1] -eq $text[0]) {
        $text = $text.Substring(1, $text.Length - 2)
    }
    if (-not $text) {
        return [pscustomobject]@{ Arguments = @(); Warning = "" }
    }

    $number = 0
    if ($text.Length -gt 12 -or $text -notmatch '^-?[0-9]+$' -or
        -not [int]::TryParse($text, [ref]$number) -or
        $number -lt -1 -or $number -gt 262144 -or $number -eq 0) {
        return [pscustomobject]@{
            Arguments = @()
            Warning = "LLAMA_ARG_CHECKPOINT_EVERY_NT=$text is not an integer from -1 to 262144 (0 is not allowed); starting llama-server without it."
        }
    }

    if (-not (Test-ODSLlamaServerHelpFlag -Executable $Executable -Flag "--checkpoint-every-n-tokens")) {
        return [pscustomobject]@{
            Arguments = @()
            Warning = "This llama-server has no --checkpoint-every-n-tokens (llama.cpp removed it in b9310); starting it without LLAMA_ARG_CHECKPOINT_EVERY_NT. Remove the setting from .env to silence this warning."
        }
    }

    return [pscustomobject]@{
        Arguments = @("--checkpoint-every-n-tokens", [string]$number)
        Warning = ""
    }
}

function Get-ODSNativeReasoningArgs {
    <#
    .SYNOPSIS
        --reasoning <mode> where the binary has it, else --reasoning-format
        (plus --reasoning-budget 0 for off where the binary has that).
    .PARAMETER Mode
        LLAMA_REASONING from .env; empty means ODS's default, off.
    .PARAMETER FallbackFormat
        The caller's --reasoning-format mapping for binaries without
        --reasoning (off -> none, on -> deepseek).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [AllowEmptyString()][string]$Mode,
        [AllowEmptyString()][string]$FallbackFormat
    )

    $value = ([string]$Mode).Trim()
    if ($value.Length -ge 2 -and ($value[0] -eq '"' -or $value[0] -eq "'") -and $value[-1] -eq $value[0]) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not $value) { $value = "off" }
    if ($value -cin @("off", "on", "auto") -and (Test-ODSLlamaServerHelpFlag -Executable $Executable -Flag "--reasoning")) {
        return @("--reasoning", $value)
    }
    $arguments = @()
    if ($FallbackFormat) { $arguments += @("--reasoning-format", $FallbackFormat) }
    if ($value -ceq "off" -and (Test-ODSLlamaServerHelpFlag -Executable $Executable -Flag "--reasoning-budget")) {
        $arguments += @("--reasoning-budget", "0")
    }
    return $arguments
}
