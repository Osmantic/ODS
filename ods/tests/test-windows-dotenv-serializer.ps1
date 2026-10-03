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
$compoundExpected = '"it''s \$HOME and \$(whoami) and ' + [char]0x02CB + 'id' + [char]0x02CB + ' and \"dq\" and C:\\path"'
Assert-Equal "single quote fallback" $compoundExpected (ConvertTo-ODSDotenvValue $compound)

# PS5.1 reads BOM-less scripts using the Windows ANSI codepage. Decode the
# actual bytes that way, then execute only the parsed serializer definition.
$sourceBytes = [IO.File]::ReadAllBytes((Join-Path $repoRoot 'installers/windows/lib/env-generator.ps1'))
foreach ($codepage in @(932, 936)) {
    $source = [Text.Encoding]::GetEncoding($codepage).GetString($sourceBytes)
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
    Assert-Equal "source parses as codepage $codepage" '0' ([string]$parseErrors.Count)
    if ($parseErrors.Count) { continue }
    $serializer = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'ConvertTo-ODSDotenvValue'
    }, $true)
    $serialized = & {
        param($Definition, $InputValue)
        . ([scriptblock]::Create($Definition))
        ConvertTo-ODSDotenvValue $InputValue
    } $serializer.Extent.Text $compound
    Assert-Equal "codepage $codepage preserves exact apostrophe fallback" $compoundExpected $serialized
}

Assert-Equal "empty value" "''" (ConvertTo-ODSDotenvValue "")
Assert-Equal "line normalization" "'line break'" (ConvertTo-ODSDotenvValue "line`nbreak")
Assert-Equal "deterministic" (ConvertTo-ODSDotenvValue 'a;b $HOME') (ConvertTo-ODSDotenvValue 'a;b $HOME')

if ($failures -gt 0) { exit 1 }
