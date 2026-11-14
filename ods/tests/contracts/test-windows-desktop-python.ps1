# Windows Desktop pinned Python: only pinned bytes are used, sys.path is fixed
# by ._pth, the runtime is published whole, and a launch can check it quickly.
# curl and pip are replaced by fixtures; only temporary files are written.
# Runs under Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$library = Join-Path $root 'installers/windows/lib/desktop-python.ps1'
. (Join-Path $root 'installers/windows/lib/private-file.ps1')
. (Join-Path $root 'installers/windows/lib/native-llama-runtime.ps1')
. $library
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
function Get-Failure([scriptblock]$Action) {
    try { & $Action; return '' } catch { return $_.Exception.Message }
}
function Write-AIWarn { param([string]$Message) }
function Get-Sha([string]$Path) { return (Get-ODSNativeLlamaFileSha256 $Path) }

# --- The committed pins are well formed and point only at python.org and PyPI ---
$real = Get-ODSDesktopPythonPins $root
Check ($real.python.file -match '^python-3\.13\.[0-9]+-embed-amd64\.zip$' -and $real.python.url.StartsWith('https://www.python.org/ftp/python/')) 'the committed Python pin is a python.org amd64 embeddable zip'
Check ($real.pip.url.StartsWith('https://files.pythonhosted.org/packages/') -and $real.pip.file -match '^pip-[0-9.]+-py3-none-any\.whl$') 'the committed pip pin is a PyPI wheel'
$pth = New-ODSDesktopPythonPathContent $real.python
Check ($pth -ceq "python313.zip`r`n.`r`nLib\site-packages`r`n" -and $pth -notmatch 'import site') 'the ._pth fixes sys.path to the runtime and never imports site'

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-desktop-python-' + [guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $fixture 'source'
$destination = Join-Path (Join-Path $fixture ('App [QA] Jos' + [char]0x00E9)) 'python'
$requirements = Join-Path $fixture 'requirements.lock'
$null = New-Item -ItemType Directory -Path (Join-Path $sourceRoot 'config') -Force
try {
    # A fake embeddable zip and pip wheel; the pins are rewritten to their bytes.
    $layout = Join-Path $fixture 'layout'
    $null = New-Item -ItemType Directory -Path $layout
    foreach ($name in 'python.exe', 'pythonw.exe', 'python313.zip', 'python3.dll') { [IO.File]::WriteAllText((Join-Path $layout $name), "fixture $name") }
    [IO.File]::WriteAllText((Join-Path $layout 'python313._pth'), "python313.zip`r`n.`r`n# Uncomment to run site.main() automatically`r`n#import site`r`n")
    $artifacts = Join-Path $fixture 'artifacts'
    $null = New-Item -ItemType Directory -Path $artifacts
    $zip = Join-Path $artifacts 'python-3.13.16-embed-amd64.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory($layout, $zip)
    $wheel = Join-Path $artifacts 'pip-26.2.1-py3-none-any.whl'
    [IO.File]::WriteAllText($wheel, 'fixture wheel')
    [IO.File]::WriteAllText($requirements, "fastapi==1.0 \`n    --hash=sha256:$('a' * 64)`n")
    function Write-Config($Python, $Pip) {
        $config = [ordered]@{ schemaVersion = 1; python = $Python; pip = $Pip }
        [IO.File]::WriteAllText((Join-Path (Join-Path $sourceRoot 'config') 'windows-desktop.json'), ($config | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    }
    $pythonPin = [ordered]@{ version = '3.13.16'; file = 'python-3.13.16-embed-amd64.zip'; size = (Get-Item -LiteralPath $zip).Length; sha256 = (Get-Sha $zip)
        url = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip'; pathFile = 'python313._pth'; zipLibrary = 'python313.zip' }
    $pipPin = [ordered]@{ version = '26.2.1'; file = 'pip-26.2.1-py3-none-any.whl'; size = (Get-Item -LiteralPath $wheel).Length; sha256 = (Get-Sha $wheel)
        url = 'https://files.pythonhosted.org/packages/f3/6e/0123/pip-26.2.1-py3-none-any.whl' }
    Write-Config $pythonPin $pipPin

    $script:downloads = @()
    $download = { param([string]$Url, [string]$Destination)
        $script:downloads += $Url
        Copy-Item -LiteralPath (Join-Path $artifacts (Split-Path -Leaf $Destination)) -Destination $Destination
        return 0 }
    # Like real pip: prints progress to stdout, writes into --target, exits 0.
    $pip = { param([string]$Python, [string]$PipWheel, [string]$Requirements, [string]$Target)
        Write-Output 'Collecting fastapi==1.0'
        Write-Output 'Successfully installed fastapi-1.0'
        $null = New-Item -ItemType Directory -Path (Join-Path $Target 'fastapi') -Force
        [IO.File]::WriteAllText((Join-Path (Join-Path $Target 'fastapi') '__init__.py'), '# fixture')
        return 0 }

    # --- Malformed pins are refused before any download ---
    foreach ($case in @(
        @{ Name = 'non-python.org host'; Python = @{ url = 'https://python.example/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip' } },
        @{ Name = 'URL not ending in the pinned file'; Python = @{ url = 'https://www.python.org/ftp/python/3.13.16/other.zip' } },
        @{ Name = 'non-embeddable zip'; Python = @{ file = 'python-3.13.16-amd64.zip'; url = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.zip' } },
        @{ Name = 'uppercase digest'; Python = @{ sha256 = ('A' * 64) } },
        @{ Name = 'size as text'; Python = @{ size = '11439094' } },
        @{ Name = 'missing ._pth name'; Python = @{ pathFile = 'python313.pth' } },
        @{ Name = 'pip outside PyPI'; Pip = @{ url = 'https://evil.example/packages/pip-26.2.1-py3-none-any.whl' } })) {
        $bad = [ordered]@{} + $pythonPin; $badPip = [ordered]@{} + $pipPin
        if ($case.Python) { foreach ($k in $case.Python.Keys) { $bad[$k] = $case.Python[$k] } }
        if ($case.Pip) { foreach ($k in $case.Pip.Keys) { $badPip[$k] = $case.Pip[$k] } }
        Write-Config $bad $badPip
        $script:downloads = @()
        $message = Get-Failure { $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $download -Pip $pip }
        Check ($message -ne '' -and $script:downloads.Count -eq 0) "a pin with a $($case.Name) is refused before downloading"
    }
    Write-Config $pythonPin $pipPin

    # --- A successful install, even though pip prints to stdout ---
    $result = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $download -Pip $pip
    Check ($result.Path -eq $destination -and $result.Python -eq '3.13.16') 'pip output on stdout does not turn a successful install into a failure'
    Check ([IO.File]::ReadAllText((Join-Path $destination 'python313._pth')) -ceq (New-ODSDesktopPythonPathContent $pythonPin)) 'the published runtime carries the locked ._pth'
    Check (Test-Path -LiteralPath (Join-Path $destination 'Lib/site-packages/fastapi/__init__.py')) 'packages land in the runtime''s own site-packages'
    $leftovers = @(Get-ChildItem -LiteralPath (Split-Path -Parent $destination) -Force | Where-Object { $_.Name -ne 'python' })
    Check ($leftovers.Count -eq 0) 'no staging folder or download is left beside the runtime'
    Check ($null -eq (Test-ODSDesktopPythonInstall -Directory $destination -Requirements $requirements)) 'the launch check accepts the fresh runtime'

    # --- Launch and repair checks ---
    $null = New-Item -ItemType Directory -Path (Join-Path $destination 'Lib/site-packages/fastapi/__pycache__') -Force
    [IO.File]::WriteAllText((Join-Path $destination 'Lib/site-packages/fastapi/__pycache__/__init__.cpython-313.pyc'), 'bytecode')
    Check ($null -eq (Test-ODSDesktopPythonInstall -Directory $destination)) 'bytecode Python writes at runtime is not a change'
    [IO.File]::WriteAllText((Join-Path $destination 'Lib/site-packages/sitecustomize.py'), 'import evil')
    Check ((Test-ODSDesktopPythonInstall -Directory $destination) -match 'unexpected file: Lib/site-packages/sitecustomize\.py') 'an added module is detected at launch'
    Remove-Item -LiteralPath (Join-Path $destination 'Lib/site-packages/sitecustomize.py')
    [IO.File]::WriteAllText((Join-Path $destination 'python3.dll'), 'fixture python3.dlX')
    Check ($null -eq (Test-ODSDesktopPythonInstall -Directory $destination)) 'the quick launch check compares sizes, not hashes'
    Check ((Test-ODSDesktopPythonInstall -Directory $destination -Full) -match 'file changed: python3\.dll') 'the repair check catches a same-size change'
    [IO.File]::WriteAllText((Join-Path $destination 'python3.dll'), 'fixture python3.dll')
    [IO.File]::WriteAllText((Join-Path $destination 'pythonw.exe'), 'fixture pythonw.exe grown')
    Check ((Test-ODSDesktopPythonInstall -Directory $destination) -match 'changed size: pythonw\.exe') 'a changed size is detected at launch'
    Remove-Item -LiteralPath (Join-Path $destination 'pythonw.exe')
    Check ((Test-ODSDesktopPythonInstall -Directory $destination) -match 'missing files') 'a deleted file is detected at launch'
    [IO.File]::AppendAllText($requirements, "# newer ODS`n")
    Check ((Test-ODSDesktopPythonInstall -Directory $destination -Requirements $requirements) -match 'older ODS version') 'a changed lock asks for a reinstall'

    # --- Failures never leave a half-replaced runtime ---
    $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $download -Pip $pip
    $before = Get-Sha (Join-Path $destination 'ods-python.json')
    $failingPip = { param($Python, $PipWheel, $Requirements, $Target) Write-Output 'ERROR: hash mismatch'; return 1 }
    $message = Get-Failure { $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $download -Pip $failingPip }
    Check ($message -match 'pip exit 1') 'a failed pip install is reported with its exit code'
    Check ((Get-Sha (Join-Path $destination 'ods-python.json')) -eq $before -and $null -eq (Test-ODSDesktopPythonInstall -Directory $destination)) 'the previous working runtime is untouched after a failed reinstall'
    $tampered = { param([string]$Url, [string]$Destination) [IO.File]::WriteAllText($Destination, 'not the pinned bytes'); return 0 }
    $message = Get-Failure { $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $tampered -Pip $pip }
    Check ($message -match 'does not match its pin') 'a download that differs from its pin is never used'
    $failedCurl = { param([string]$Url, [string]$Destination) return 35 }
    Check ((Get-Failure { $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $failedCurl -Pip $pip }) -match 'curl exit 35') 'a failed download is reported with curl''s exit code'
    $leftovers = @(Get-ChildItem -LiteralPath (Split-Path -Parent $destination) -Force | Where-Object { $_.Name -ne 'python' })
    Check ($leftovers.Count -eq 0) 'failed installs leave no staging folder behind'
    Remove-Item -LiteralPath (Join-Path $layout 'python313._pth')
    Remove-Item -LiteralPath $zip
    [IO.Compression.ZipFile]::CreateFromDirectory($layout, $zip)
    $pythonPin.size = (Get-Item -LiteralPath $zip).Length; $pythonPin.sha256 = Get-Sha $zip
    Write-Config $pythonPin $pipPin
    Check ((Get-Failure { $null = Install-ODSDesktopPython -SourceRoot $sourceRoot -Destination $destination -Requirements $requirements -Download $download -Pip $pip }) -match 'embeddable layout') 'an archive without the embeddable layout is refused'

    # --- Every path call in the library is literal ---
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($library, [ref]$tokens, [ref]$errors)
    Check ($errors.Count -eq 0) 'desktop-python.ps1 parses without errors'
    $pathCommands = 'Test-Path', 'Get-Item', 'Get-ChildItem', 'Remove-Item', 'Move-Item', 'Copy-Item', 'Get-Content', 'Set-Content', 'Add-Content'
    $wildcard = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in $pathCommands -and
        -not ($node.CommandElements | Where-Object { $_ -is [Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'LiteralPath' })
    }, $true))
    Check ($wildcard.Count -eq 0) 'every path cmdlet uses -LiteralPath'
    Check (-not ([IO.File]::ReadAllBytes($library) | Where-Object { $_ -gt 127 })) 'desktop-python.ps1 is ASCII-only for Windows PowerShell 5.1'
} finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
Microsoft.PowerShell.Utility\Write-Host "All $script:checks Windows Desktop Python checks passed."
