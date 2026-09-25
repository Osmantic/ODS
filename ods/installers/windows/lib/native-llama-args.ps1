# ============================================================================
# ODS Windows Installer -- native llama-server argument qualification
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Pass opt-in llama-server tuning only when the installed binary
#          accepts it.
#
# llama-server exits with "invalid argument" on a flag it does not know.
# llama.cpp removed --checkpoint-every-n-tokens in b9310 and replaced it with
# --checkpoint-min-step, which has different semantics. The Windows launchers
# (install-windows.ps1, ods.ps1) therefore pass LLAMA_ARG_CHECKPOINT_EVERY_NT
# only after the same checks installers/macos/lib/native-checkpoint-args.py
# makes: an integer from -1 to 262144 other than 0, and a flag that the
# binary's own --help lists. scripts/bootstrap-upgrade.sh does the same in
# Bash for its Windows hot-swap.
#
# An unusable value is dropped with a warning instead of stopping the start:
# the setting is opt-in, and no shipped profile sets it.
# ============================================================================

function Test-ODSLlamaServerHelpFlag {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Flag,
        [int]$TimeoutSeconds = 15
    )

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }

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
        return $false
    }
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return $false
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { return $false }
        $helpText = [string]$stdout.Result + [string]$stderr.Result
    } finally {
        $process.Dispose()
    }
    if ($helpText.Length -gt 1MB) { return $false }
    $pattern = '(?<![\w-])' + [regex]::Escape($Flag) + '(?![\w-])'
    return [regex]::IsMatch($helpText, $pattern)
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
