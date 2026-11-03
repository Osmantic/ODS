[CmdletBinding()]
param()
# Contract: syncing the Windows OpenCode config keeps the owner's existing
# settings when the config folder holds wildcard characters ([ ]), and reads
# the file as UTF-8 under Windows PowerShell 5.1, whose Get-Content default
# is the ANSI code page.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'installers/windows/lib/opencode-config.ps1')
$checks = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}

$utf8 = [Text.UTF8Encoding]::new($false)
$endpoint = @{ BaseUrl = 'http://127.0.0.1:8080/v1' }
$ownerName = "Local $([char]0x2014) caf$([char]0x00E9)"
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-opencode-[lab]-' + [guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($tempRoot)
try {
    foreach ($fileName in @('opencode.json', 'config.json')) {
        Get-ChildItem -LiteralPath $tempRoot -Force | Remove-Item -Force
        $ownerConfig = '{"theme":"owner-theme","provider":{"owner":{"name":"' + $ownerName + '"}}}'
        [IO.File]::WriteAllText((Join-Path $tempRoot $fileName), $ownerConfig, $utf8)

        $result = Sync-WindowsOpenCodeConfig -LlmEndpoint $endpoint -ModelId 'fixture.gguf' `
            -ModelName 'fixture' -ContextLimit 16384 -ConfigDir $tempRoot
        Assert-True ($result.Status -ceq 'updated') "${fileName}: expected status 'updated', got '$($result.Status)'"

        foreach ($written in @('opencode.json', 'config.json')) {
            $config = [IO.File]::ReadAllText((Join-Path $tempRoot $written), $utf8) | ConvertFrom-Json
            Assert-True ($config.theme -ceq 'owner-theme') "${fileName} -> ${written}: the owner's theme was dropped"
            Assert-True ($config.provider.owner.name -ceq $ownerName) `
                "${fileName} -> ${written}: owner provider name became '$($config.provider.owner.name)'"
            Assert-True ($config.model -ceq 'llama-server/fixture.gguf') "${fileName} -> ${written}: model was not set"
        }
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
Write-Host "[PASS] Windows OpenCode config sync keeps owner settings on bracketed paths and as UTF-8 ($checks checks)"
