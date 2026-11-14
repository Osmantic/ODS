# Stop only Python running this installation's host-agent script. A stale PID
# or an unrelated listener must not prevent starting a replacement agent.
function Stop-ODSHostAgentProcess {
    param(
        [int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$AgentScript
    )

    if ($ProcessId -le 0) { return }
    $candidate = $null
    try {
        $candidate = Get-Process -Id $ProcessId -ErrorAction Stop
        # Retain the process handle before inspecting ownership, so termination
        # cannot target a different process if the numeric PID is later reused.
        $null = $candidate.Handle
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if (-not $record -or $record.Name -notmatch '^pythonw?(?:\d+(?:\.\d+)*)?\.exe$') { return }

        # The shipped launcher passes the script immediately after Python.
        # Anchor both arguments: an arbitrary command containing this path,
        # such as python -c, is not an owned host-agent process.
        $arguments = [regex]::Match([string]$record.CommandLine,
            '^\s*(?:"[^"]+"|\S+)\s+(?:"(?<script>[^"]+)"|(?<script>\S+))(?:\s|$)')
        if (-not $arguments.Success) { return }
        $scriptArgument = $arguments.Groups['script'].Value
        # The shipped launcher uses an absolute script path. A relative or
        # drive-relative argument belongs to the target process's working
        # directory, which CIM does not expose; resolving it against ours
        # could mistake another installation's process for this agent.
        if ($scriptArgument -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+[\\/])') { return }
        $actualScript = [IO.Path]::GetFullPath($scriptArgument)
        $expectedScript = [IO.Path]::GetFullPath($AgentScript)
        if (-not $actualScript.Equals($expectedScript, [StringComparison]::OrdinalIgnoreCase)) { return }

        Stop-Process -InputObject $candidate -Force -ErrorAction Stop
    } catch {
        # Missing/inaccessible process metadata never authorizes termination.
        # Callers discard stale receipts and keep the normal recovery path.
    } finally {
        if ($candidate) { $candidate.Dispose() }
    }
}

function ConvertTo-ODSHostAgentArgumentLine {
    <#
    .SYNOPSIS
        Build the host agent's Start-Process argument line with each value quoted.
        Windows PowerShell 5.1 joins an -ArgumentList array with spaces and no
        quoting, so an install dir under a user profile with a space
        (C:\Users\John Smith\ods) reached python as two arguments and the agent
        exited at once. Quoting follows the Windows CRT argv rules.
    #>
    param(
        [AllowEmptyCollection()][string[]]$PrefixArgs = @(),
        [Parameter(Mandatory = $true)][string]$AgentScript,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][string]$InstallDir
    )
    $values = @($PrefixArgs) + @($AgentScript, '--port', [string]$Port, '--pid-file', $PidFile, '--install-dir', $InstallDir)
    return (@($values | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace([string]$_, '(\*)"', '$1$1\"'), '(\+)$', '$1$1') + '"'
    }) -join ' ')
}
