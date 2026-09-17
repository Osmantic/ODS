$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $repoRoot "installers/windows/lib/env-generator.ps1")

$failures = 0
function Assert-Equal([string]$Label, [string]$Expected, [string]$Actual) {
    if ($Expected -ne $Actual) {
        Write-Host "FAIL: $Label expected=[$Expected] actual=[$Actual]"
        $script:failures++
    } else {
        Write-Host "PASS: $Label"
    }
}

$simple = 'deepseek-r1:32768:48;qwen-a3b:131072:35.48'
Assert-Equal "simple value" "'$simple'" (ConvertTo-ODSDotenvValue $simple)

$special = 'cost is $HOME and $(whoami) and `id` and "dq" and C:\path'
Assert-Equal "literal special characters" "'$special'" (ConvertTo-ODSDotenvValue $special)

$compound = 'it''s $HOME and $(whoami) and `id` and "dq" and C:\path'
$compoundExpected = '"it''s \$HOME and \$(whoami) and ˋidˋ and \"dq\" and C:\\path"'
Assert-Equal "single quote fallback" $compoundExpected (ConvertTo-ODSDotenvValue $compound)

Assert-Equal "empty value" "''" (ConvertTo-ODSDotenvValue "")
Assert-Equal "line normalization" "'line break'" (ConvertTo-ODSDotenvValue "line`nbreak")
Assert-Equal "deterministic" (ConvertTo-ODSDotenvValue 'a;b $HOME') (ConvertTo-ODSDotenvValue 'a;b $HOME')

if ($failures -gt 0) { exit 1 }
