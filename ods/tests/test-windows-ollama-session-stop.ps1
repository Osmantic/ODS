$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$phasePath = Join-Path $root "installers\windows\phases\01-preflight.ps1"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $phasePath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) { throw "Windows preflight failed to parse: $($errors[0].Message)" }

$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Invoke-ODSWindowsOllamaConflictPrompt"
}, $true)
if (-not $functionAst) { throw "Ollama conflict handler not found in Windows preflight" }
. ([scriptblock]::Create($functionAst.Extent.Text))

$script:ollamaRunning = $true
$script:stopCalls = 0
$script:removedPaths = @()
$script:warnings = @()
$script:messages = @()
$env:APPDATA = "C:\Users\TestUser\AppData\Roaming"

function Get-Process {
    param([string]$Name, $ErrorAction)
    if ($Name -eq "ollama" -and $script:ollamaRunning) {
        return [pscustomobject]@{ Id = 1234; ProcessName = "ollama" }
    }
    return $null
}
function Stop-Process {
    param([string]$Name, [switch]$Force, $ErrorAction)
    $script:stopCalls++
    # Simulate an Ollama service that immediately restarts the app.
    $script:ollamaRunning = $true
}
function Start-Sleep { param([int]$Seconds) }
function Read-Host { param([string]$Prompt); "y" }
function Test-Path {
    param([string]$Path)
    return $Path -like "*\Startup\Ollama.lnk"
}
function Remove-Item {
    param([string]$Path, [switch]$Force, $ErrorAction)
    $script:removedPaths += $Path
}
function Write-AIWarn { param([string]$Message); $script:warnings += $Message }
function Write-AI { param([string]$Message); $script:messages += $Message }
function Write-AISuccess { param([string]$Message) }

Invoke-ODSWindowsOllamaConflictPrompt -NonInteractive $false

if ($script:stopCalls -ne 1) { throw "Expected one session-scoped Ollama stop attempt; got $script:stopCalls" }
if ($script:removedPaths.Count -ne 0) { throw "The session-only prompt removed a Startup entry: $($script:removedPaths -join ', ')" }
if (-not ($script:messages -match "Startup shortcut was left unchanged")) {
    throw "Expected the handler to explain that the Startup shortcut remains unchanged"
}

Write-Output "PASS interactive Ollama session stop retains the user's Startup shortcut"
