$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$phasePath = Join-Path $root "installers/windows/phases/06-directories.ps1"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $phasePath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Could not parse Windows directory phase: $($errors[0].Message)"
}

$functionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Update-HermesConfigFile"
    },
    $true
)
if (-not $functionAst) {
    throw "Update-HermesConfigFile was not found"
}
Invoke-Expression $functionAst.Extent.Text

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-hermes-config-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    $fixtures = @(
        @{
            Name = "quoted-template"
            Content = @"
model:
  default: "old-model"
  provider: "custom"
  base_url: "http://old.invalid/v1"
  api_key: "old-key"
  context_length: 4096
providers:
  custom:
    request_timeout_seconds: 180
auxiliary:
  compression:
    context_length: 4096
terminal:
  backend: local
"@
        },
        @{
            Name = "unquoted-live"
            Content = @"
model:
  default: old-model
  provider: custom
  base_url: http://old.invalid/v1
  context_length: 4096
providers:
  custom:
    request_timeout_seconds: 180
auxiliary:
  compression:
    context_length: 4096
terminal:
  backend: local
"@
        }
    )

    foreach ($fixture in $fixtures) {
        $path = Join-Path $tempRoot ($fixture.Name + ".yaml")
        [System.IO.File]::WriteAllText($path, $fixture.Content, (New-Object System.Text.UTF8Encoding($false)))
        $updated = Update-HermesConfigFile `
            -Path $path `
            -Model "ods/current" `
            -BaseUrl "http://litellm:4000/v1" `
            -ApiKey "sk-test-hermes-runtime" `
            -ContextLength 65536 `
            -RequestTimeoutSeconds 900
        if (-not $updated) {
            throw "Hermes config update returned false for $($fixture.Name)"
        }
        $actual = [System.IO.File]::ReadAllText($path)
        if (-not $actual.Contains('  default: "ods/current"')) {
            throw "Model was not updated for $($fixture.Name)"
        }
        if (-not $actual.Contains('  base_url: "http://litellm:4000/v1"')) {
            throw "Base URL was not updated for $($fixture.Name)"
        }
        if (-not $actual.Contains('  api_key: "sk-test-hermes-runtime"')) {
            throw "API key was not inserted or updated for $($fixture.Name)"
        }
        if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
            $acl = Get-Acl -LiteralPath $path
            if (-not $acl.AreAccessRulesProtected) {
                throw "Credential file retained inherited access for $($fixture.Name)"
            }
            $everyoneSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-1-0")
            foreach ($rule in $acl.GetAccessRules(
                $true,
                $true,
                [System.Security.Principal.SecurityIdentifier]
            )) {
                if ($rule.IdentityReference -eq $everyoneSid -and
                    $rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow) {
                    throw "Credential file retained an Everyone allow rule for $($fixture.Name)"
                }
            }
        }
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Windows Hermes config runtime contract passed"
