# Windows Desktop model acquisition: a slow or interrupted download always
# resumes instead of starting over, nothing is hashed twice, and a launch or
# rerun trusts the verified stamp. curl is replaced by fixtures; only temporary
# files are written. Runs under Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$library = Join-Path $root 'installers/windows/lib/desktop-model.ps1'
. $library
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
$script:output = @()
function Write-Host { param([Parameter(ValueFromRemainingArguments = $true)]$Text) $script:output += ,([string]($Text -join ' ')) }

# Count every hash so "nothing is hashed twice" is a measured fact.
$script:hashes = 0
$realHash = ${function:Get-ODSDesktopFileSha256}
function Get-ODSDesktopFileSha256([string]$Path) { $script:hashes++; return (& $realHash $Path) }

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-model-' + [guid]::NewGuid().ToString('N'))
# Brackets are PowerShell wildcards and the accent is a non-ASCII profile name.
$models = Join-Path $fixture ('Models [QA] Jos' + [char]0x00E9)
$null = New-Item -ItemType Directory -Path $fixture
try {
    $content = [byte[]]::new(300000)
    (New-Object Random 42).NextBytes($content)
    $sha = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($content)) -replace '-', '').ToLowerInvariant()
    $pin = [pscustomobject]@{
        File = 'Tiny-Q4_K_M.gguf'; Sha256 = $sha; Size = [long]$content.Length
        Url = 'https://huggingface.co/example/tiny/resolve/0123456789abcdef/Tiny-Q4_K_M.gguf'
    }
    $target = Join-Path $models $pin.File
    $partial = "$target.partial"
    $stamp = "$target.ods-verified.json"

    # A fake curl that appends the next slice of the model and returns an exit
    # code, like curl --continue-at - after a stall or a dropped connection.
    function New-FakeDownload([int[]]$Codes, [int]$ChunkCount = 3, [byte[]]$Payload = $content) {
        $state = @{ calls = 0 }
        $block = {
            param([string]$Url, [string]$Partial)
            $state.calls++
            $have = if (Test-Path -LiteralPath $Partial) { (Get-Item -LiteralPath $Partial).Length } else { 0 }
            $step = [math]::Ceiling($Payload.Length / $ChunkCount)
            $take = [math]::Min($step, $Payload.Length - $have)
            if ($take -gt 0) {
                $stream = [IO.File]::Open($Partial, [IO.FileMode]::Append, [IO.FileAccess]::Write)
                try { $stream.Write($Payload, [int]$have, [int]$take) } finally { $stream.Dispose() }
            }
            return $Codes[[math]::Min($state.calls - 1, $Codes.Count - 1)]
        }.GetNewClosure()
        return @{ Block = $block; State = $state }
    }

    # --- Pin validation: nothing is fetched from an unexpected place ---
    foreach ($bad in @(
        @{ File = '..\evil.gguf'; Url = $pin.Url; Sha256 = $sha; Size = 1 },
        @{ File = 'model.bin'; Url = $pin.Url; Sha256 = $sha; Size = 1 },
        @{ File = $pin.File; Url = 'http://huggingface.co/x/model.gguf'; Sha256 = $sha; Size = 1 },
        @{ File = $pin.File; Url = 'https://huggingface.co.evil.example/x/model.gguf'; Sha256 = $sha; Size = 1 },
        @{ File = $pin.File; Url = 'https://huggingface.co/x/model.gguf?redirect=evil'; Sha256 = $sha; Size = 1 },
        @{ File = $pin.File; Url = $pin.Url; Sha256 = $sha.ToUpperInvariant(); Size = 1 },
        @{ File = $pin.File; Url = $pin.Url; Sha256 = ''; Size = 1 },
        @{ File = $pin.File; Url = $pin.Url; Sha256 = $sha; Size = 0 })) {
        Check ((Get-Failure { Assert-ODSDesktopModelPin ([pscustomobject]$bad) }) -ne '') "a malformed pin is refused ($($bad.File) $($bad.Url) size $($bad.Size))"
    }
    Assert-ODSDesktopModelPin ([pscustomobject]@{ File = $pin.File; Url = $pin.Url; Sha256 = $sha; Size = $null })
    Check $true 'a pin without a size (five catalog models have none) is accepted; its hash still must match'

    # --- First download: interrupted twice, resumed, verified once ---
    $fake = New-FakeDownload -Codes @(28, 56, 0)
    $script:hashes = 0
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($fake.State.calls -eq 3) 'two interrupted attempts resume instead of failing setup'
    Check ([IO.File]::ReadAllBytes($target).Length -eq $content.Length -and
        (Get-ODSDesktopFileSha256 $target) -eq $sha) 'the resumed file is byte-identical to the pinned model'
    $script:hashes--  # the check above hashed once itself
    Check ($script:hashes -eq 1) 'the completed download is hashed exactly once'
    Check ($result.Downloaded -and $result.Hashed -and $result.Path -eq $target) 'the result reports a fresh, verified download'
    Check (-not (Test-Path -LiteralPath $partial)) 'the partial file is published, not left behind'
    Check (Test-Path -LiteralPath $stamp) 'a verified stamp is written beside the model'
    Check (($script:output -join "`n") -match 'Resuming \(1/') 'the owner sees that the download resumed'

    # --- Rerun or launch: the stamp means no download and no hashing ---
    $script:hashes = 0
    $fake = New-FakeDownload -Codes @(0)
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($script:hashes -eq 0 -and $fake.State.calls -eq 0 -and -not $result.Hashed) 'a verified model is neither hashed nor downloaded again'

    # --- A changed file, a torn stamp, a new pin or -Repair each re-hash once ---
    (Get-Item -LiteralPath $target).LastWriteTimeUtc = (Get-Date).ToUniversalTime().AddMinutes(-5)
    $script:hashes = 0
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($script:hashes -eq 1 -and $result.Hashed -and -not $result.Downloaded) 'a model touched since its stamp is hashed once, not downloaded'
    [IO.File]::WriteAllText($stamp, '{"schemaVersion":1,', [Text.UTF8Encoding]::new($false))
    $script:hashes = 0
    $null = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($script:hashes -eq 1) 'a torn stamp costs one re-hash, not a failure'
    $script:hashes = 0
    $null = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block -Repair
    Check ($script:hashes -eq 1 -and $fake.State.calls -eq 0) 'Repair re-verifies the model without downloading it'

    # --- A corrupt model is replaced; a wrong download never replaces it ---
    $corrupt = [byte[]]$content.Clone(); $corrupt[1000] = $corrupt[1000] -bxor 0xFF
    [IO.File]::WriteAllBytes($target, $corrupt)
    $fake = New-FakeDownload -Codes @(0) -ChunkCount 1
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($result.Downloaded -and (Get-ODSDesktopFileSha256 $target) -eq $sha) 'a same-size corrupt model is detected and replaced'
    [IO.File]::WriteAllBytes($target, $corrupt)
    Remove-Item -LiteralPath $stamp
    $fake = New-FakeDownload -Codes @(0) -ChunkCount 1 -Payload $corrupt
    $message = Get-Failure { $null = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block }
    Check ($message -match 'does not match its checksum') 'a complete download with the wrong hash is refused'
    Check (-not (Test-Path -LiteralPath $partial)) 'a provably wrong partial is deleted so the next run starts clean'
    [IO.File]::WriteAllBytes($target, $content)

    # --- An oversize partial can never verify, so it is discarded first ---
    Remove-Item -LiteralPath $target
    [IO.File]::WriteAllBytes($partial, [byte[]]::new($content.Length + 10))
    $fake = New-FakeDownload -Codes @(0) -ChunkCount 1
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($result.Downloaded -and (Get-ODSDesktopFileSha256 $target) -eq $sha) 'a partial larger than the pin is discarded before resuming'

    # --- Giving up keeps every byte already downloaded ---
    Remove-Item -LiteralPath $target, $stamp
    $fake = New-FakeDownload -Codes @(28) -ChunkCount 10
    $message = Get-Failure { $null = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block -Attempts 3 }
    Check ($fake.State.calls -eq 3 -and $message -match 'after 3 attempts' -and $message -match 'is kept') 'after the last stalled attempt the error says the progress is kept'
    Check ((Get-Item -LiteralPath $partial).Length -eq 3 * [math]::Ceiling($content.Length / 10)) 'the partial survives a failed setup for the next run to resume'
    # One chunk now delivers everything the kept partial is missing.
    $fake = New-FakeDownload -Codes @(0) -ChunkCount 1
    $null = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $fake.Block
    Check ($fake.State.calls -eq 1 -and (Get-ODSDesktopFileSha256 $target) -eq $sha) 'the next run continues from the kept partial'

    # --- A download step that prints before its exit code still succeeds ---
    Remove-Item -LiteralPath $target, $stamp
    $talkative = { param([string]$Url, [string]$Partial)
        Write-Output '  % Total    % Received % Xferd  Average Speed'
        [IO.File]::WriteAllBytes($Partial, $content)
        return 0 }
    $result = Get-ODSDesktopModel -Pin $pin -ModelsDir $models -Download $talkative
    Check ($result.Downloaded -and (Get-ODSDesktopFileSha256 $target) -eq $sha) 'output printed by a download step is never read as its exit code'

    # --- Every path call in the library is literal ---
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($library, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) 'desktop-model.ps1 parses without errors'
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($wildcard.Count -eq 0) ('every path cmdlet uses -LiteralPath, so [ in a folder name is never a wildcard' + ($wildcard | ForEach-Object { " :: $($_.Extent.Text)" }))
    Check (-not ([IO.File]::ReadAllBytes($library) | Where-Object { $_ -gt 127 })) 'desktop-model.ps1 is ASCII-only for Windows PowerShell 5.1'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop model checks passed."
