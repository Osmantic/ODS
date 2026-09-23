# This test invokes only the local-file verifier, never the installer branch.
# Download/execution ordering is inspected as AST; no services or MSI are run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root "installers/windows/install-windows.ps1"
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $installerPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count) { throw "Installer parse failed: $($parseErrors[0].Message)" }

$script:checks = 0
function Assert-True {
    param([bool]$Value, [string]$Label)
    if (-not $Value) { throw $Label }
}
function Assert-Rejected {
    param([scriptblock]$Action, [string]$Pattern, [string]$Label)
    $caught = $null
    try { $null = & $Action } catch { $caught = $_ }
    Assert-True ($null -ne $caught) "$Label unexpectedly accepted"
    $errorIdentity = $caught.Exception.Message + " " + $caught.FullyQualifiedErrorId
    Assert-True ($errorIdentity -match $Pattern) "$Label failed for an unrelated reason: $caught"
    $script:checks++
}

$verifiers = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Test-ODSVerifiedLemonadeMsi"
}, $true))
Assert-True ($verifiers.Count -eq 1) "Expected one local-file verifier"
$verifier = $verifiers[0]
$commands = @($verifier.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
Assert-True ($commands.Count -eq 2) "Verifier gained unexpected commands"
foreach ($command in $commands) {
    Assert-True ($command.GetCommandName() -in @("Get-Item", "Get-FileHash")) `
        "Verifier must only inspect local file metadata/bytes"
    Assert-True ($command.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Unknown) `
        "Verifier must not invoke a script or external command indirectly"
}
Assert-True (@($verifier.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.InvokeMemberExpressionAst]
}, $true)).Count -eq 0) "Verifier must not invoke hidden .NET side effects"
# Load this one verified function definition. Never dot-source the orchestrator.
. ([scriptblock]::Create($verifier.Extent.Text))

$contract = Get-Content -LiteralPath (Join-Path $root "config/backends/amd.json") -Raw | ConvertFrom-Json
Assert-True ($contract.runtime.lemonade.windows_msi_sha256 -cmatch '\A[0-9a-f]{64}\z') `
    "AMD contract needs a reviewed SHA-256"
$bindings = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left.Extent.Text -eq '$script:LEMONADE_MSI_SHA256'
}, $true))
Assert-True ($bindings.Count -eq 1) "Expected one contract hash binding"
Assert-True ($bindings[0].Right.Extent.Text -eq '[string]$amdLemonadeRuntime.windows_msi_sha256') `
    "Installer must use the committed contract hash, not install-time metadata"
$script:checks++

# Inspect the real activation branch without executing it.
$branches = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -eq '$lemonadeChoice -match "^[Yy]"'
}, $true))
Assert-True ($branches.Count -eq 1) "Expected exactly one Lemonade installation branch"
$branch = $branches[0]
$download = @($branch.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq "Invoke-DownloadWithRetry"
}, $true))
$verify = @($branch.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq "Test-ODSVerifiedLemonadeMsi"
}, $true))
$execute = @($branch.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq "Start-Process"
}, $true))
Assert-True ($download.Count -eq 1 -and $verify.Count -eq 1 -and $execute.Count -eq 1) `
    "Expected one download, verification and process boundary"
Assert-True ($download[0].Extent.EndOffset -lt $verify[0].Extent.StartOffset -and
    $verify[0].Extent.EndOffset -lt $execute[0].Extent.StartOffset) `
    "MSI must be downloaded, verified, then activated in that order"
Assert-True ($verify[0].Extent.Text -match '-Path \$msiPath' -and
    $verify[0].Extent.Text -match '-ExpectedSha256 \$script:LEMONADE_MSI_SHA256') `
    "Verification must bind the actual staging path to the contract hash"
$verifyAssignment = $verify[0].Parent
while ($verifyAssignment -and $verifyAssignment -isnot [System.Management.Automation.Language.AssignmentStatementAst]) {
    $verifyAssignment = $verifyAssignment.Parent
}
Assert-True ($verifyAssignment.Left.Extent.Text -eq '$dlOk') "Verification must replace download success"
$verifyTry = $verifyAssignment.Parent
while ($verifyTry -and $verifyTry -isnot [System.Management.Automation.Language.TryStatementAst]) {
    $verifyTry = $verifyTry.Parent
}
Assert-True ($null -ne $verifyTry -and $verifyTry.CatchClauses.Count -eq 1) "Verification errors must be caught"
$catchAssignments = @($verifyTry.CatchClauses[0].FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left.Extent.Text -eq '$dlOk' -and $node.Right.Extent.Text -eq '$false'
}, $true))
Assert-True ($catchAssignments.Count -eq 1) "Failed verification must clear the activation guard"
$activationGuard = $execute[0].Parent
while ($activationGuard -and -not (
    $activationGuard -is [System.Management.Automation.Language.IfStatementAst] -and
    $activationGuard.Clauses[0].Item1.Extent.Text -eq '$dlOk'
)) { $activationGuard = $activationGuard.Parent }
Assert-True ($null -ne $activationGuard -and $verifyTry.Extent.EndOffset -lt $activationGuard.Extent.StartOffset) `
    "Process activation must be guarded after verification and its failure handler"
$unexpectedAssignments = @($branch.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left.Extent.Text -eq '$dlOk' -and
        $node.Extent.StartOffset -gt $verifyTry.Extent.EndOffset -and
        $node.Extent.StartOffset -lt $execute[0].Extent.StartOffset
}, $true))
Assert-True ($unexpectedAssignments.Count -eq 0) "Activation must not override verification failure"
$executeTry = $execute[0].Parent
while ($executeTry -and $executeTry -isnot [System.Management.Automation.Language.TryStatementAst]) {
    $executeTry = $executeTry.Parent
}
Assert-True ($null -ne $executeTry.Finally -and
    $executeTry.Finally.Extent.Text -match 'Remove-Item -LiteralPath \$msiPath') `
    "MSI staging must be cleaned if process startup throws"
$script:checks++

# These are inert text bytes, not an MSI package. All file operations stay here.
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-msi-hash-test-" + [guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $testRoot
$fixture = Join-Path $testRoot "fixture bytes.msi"
$empty = Join-Path $testRoot "empty.msi"
try {
    [System.IO.File]::WriteAllBytes($fixture, [System.Text.Encoding]::UTF8.GetBytes("reviewed inert fixture bytes"))
    [System.IO.File]::WriteAllBytes($empty, [byte[]]@())
    $expected = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-True (Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $expected) "Matching bytes should verify"
    $script:checks++
    Assert-True (Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $expected.ToUpperInvariant()) "Uppercase SHA-256 should verify"
    $script:checks++
    foreach ($invalid in @("", "1234", ("g" * 64), (" " + $expected), ($expected + "`n"))) {
        Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $invalid } `
            'no valid reviewed SHA-256' "Malformed or missing hash"
    }
    Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 ("0" * 64) } `
        'does not match' "Wrong digest"
    Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $empty -ExpectedSha256 $expected } `
        'not a regular nonempty' "Empty file"
    Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $testRoot -ExpectedSha256 $expected } `
        'not a regular nonempty' "Directory"
    Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path (Join-Path $testRoot "absent.msi") -ExpectedSha256 $expected } `
        'PathNotFound,Microsoft.PowerShell.Commands.GetItemCommand' "Missing file"
    [System.IO.File]::WriteAllBytes($fixture, [System.Text.Encoding]::UTF8.GetBytes("substituted inert fixture bytes"))
    Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $expected } `
        'does not match' "Substituted download bytes"
    # Simulate an unreadable file and a reparse point without needing ACL/symlink privileges.
    function Get-FileHash { throw "fixture hash read failure" }
    try {
        Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $expected } `
            'fixture hash read failure' "Hash I/O failure"
    } finally { Remove-Item Function:Get-FileHash }
    function Get-Item {
        [pscustomobject]@{ PSIsContainer = $false; Length = 42; Attributes = [System.IO.FileAttributes]::ReparsePoint }
    }
    try {
        Assert-Rejected { Test-ODSVerifiedLemonadeMsi -Path $fixture -ExpectedSha256 $expected } `
            'not a regular nonempty' "Reparse point"
    } finally { Remove-Item Function:Get-Item }
} finally {
    # Explicit files and an empty directory only; no recursive deletion.
    foreach ($path in @($fixture, $empty)) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $testRoot -Force
}
Write-Output "PASS: $script:checks Lemonade MSI integrity checks (local bytes and AST only; no installer execution)"
