$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$phasePath = Join-Path $root 'installers/windows/phases/01-preflight.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($phasePath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$ollamaBranch = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -eq '$_ollamaProc'
}, $true)
if (-not $ollamaBranch) { throw 'Ollama preflight branch not found' }
$branch = [scriptblock]::Create($ollamaBranch.Extent.Text)

function Get-Process {
    param($Name, $ErrorAction)
    if ($Name -ne 'ollama') { throw 'Unexpected process lookup' }
    if ($script:processPresent) { return [pscustomobject]@{ Id = 1234 } }
}
function Stop-Process {
    param($Name, [switch]$Force, $ErrorAction)
    if ($Name -ne 'ollama') { throw 'Unexpected process stop' }
    $script:stopCalls++
    $script:processPresent = $script:restartAfterStop
}
function Read-Host { param($Prompt) $script:promptCalls++; return $script:choice }
function Start-Sleep { param($Seconds) }
function Write-AI { param($Message) }
function Write-AIWarn { param($Message) $script:warnings += [string]$Message }
function Write-AISuccess { param($Message) }
function Remove-Item { throw 'Ollama preflight must not remove a user-owned Startup entry' }
function Test-Path { throw 'Ollama preflight must not inspect a user-owned Startup entry' }

function Invoke-Case {
    param([bool]$Present, [bool]$NonInteractive, [string]$Choice, [bool]$Restart)
    $script:processPresent = $Present
    $script:restartAfterStop = $Restart
    $script:choice = $Choice
    $script:stopCalls = 0
    $script:promptCalls = 0
    $script:warnings = @()
    $nonInteractive = $NonInteractive
    $_ollamaProc = Get-Process -Name 'ollama' -ErrorAction SilentlyContinue
    . $branch
    return [pscustomobject]@{
        StopCalls = $script:stopCalls
        PromptCalls = $script:promptCalls
        ProcessPresent = $script:processPresent
        Warnings = @($script:warnings)
    }
}

$result = Invoke-Case -Present $false -NonInteractive $false -Choice 'y' -Restart $false
if ($result.StopCalls -ne 0 -or $result.PromptCalls -ne 0) { throw 'Absent Ollama triggered a prompt or stop' }

foreach ($choice in @('', 'n', 'no', 'unexpected')) {
    $result = Invoke-Case -Present $true -NonInteractive $false -Choice $choice -Restart $false
    if ($result.StopCalls -ne 0 -or $result.PromptCalls -ne 1 -or -not $result.ProcessPresent) {
        throw "Choice '$choice' changed user-owned Ollama"
    }
}

$result = Invoke-Case -Present $true -NonInteractive $true -Choice 'y' -Restart $false
if ($result.StopCalls -ne 0 -or $result.PromptCalls -ne 0 -or -not $result.ProcessPresent) {
    throw 'Noninteractive install changed user-owned Ollama'
}

$result = Invoke-Case -Present $true -NonInteractive $false -Choice 'yes' -Restart $false
if ($result.StopCalls -ne 1 -or $result.PromptCalls -ne 1 -or $result.ProcessPresent) {
    throw 'Explicit yes failed to stop Ollama for this session'
}

$result = Invoke-Case -Present $true -NonInteractive $false -Choice 'y' -Restart $true
if ($result.StopCalls -ne 1 -or -not $result.ProcessPresent -or
    -not (@($result.Warnings | Where-Object { $_ -match 'Startup unchanged' }).Count -eq 1)) {
    throw 'Auto-restarting Ollama was stopped again or lost the Startup warning'
}

Write-Host '[PASS] Windows preflight leaves Ollama and Startup untouched by default; explicit stop is session-only'
exit 0
