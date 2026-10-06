# Pure-function contracts for the Windows Portal prerequisites.
# No WSL, Docker, registry, downloads or prompts are touched.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-prereqs.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}

foreach ($name in @('maria', 'joao-silva', 'dev_1', 'a')) { Check (Test-ODSPortalLinuxUsername $name) "accepts Linux username $name" }
foreach ($name in @('', 'Maria', '1abc', 'root', 'ods', 'docker', 'with space', 'joão', ('a' * 33), 'x;rm')) { Check (-not (Test-ODSPortalLinuxUsername $name)) "rejects Linux username '$name'" }

Check ((Get-ODSPortalDistroLauncherName 'Ubuntu-24.04') -eq 'ubuntu2404.exe') 'maps Ubuntu-24.04 to its launcher'
Check ((Get-ODSPortalDistroLauncherName 'Ubuntu') -eq 'ubuntu.exe') 'maps Ubuntu to its launcher'
Check ($null -eq (Get-ODSPortalDistroLauncherName 'Debian')) 'no launcher guess for other distros'

foreach ($case in @(
    @('Ubuntu-24.04', 'Ubuntu-24.04'),
    @('Ubuntu Dev', '"Ubuntu Dev"'),
    @('', '""'),
    @('a"b', '"a\"b"'),
    @('C:\with space\', '"C:\with space\\"'))) {
    Check ((ConvertTo-ODSPortalProcessArgument $case[0]) -ceq $case[1]) 'process arguments preserve spaces, quotes and trailing backslashes'
}

$capturedConfig = & {
    function Invoke-ODSPortalWslInput([string]$Distro, [string[]]$Command, [string]$Text) {
        [pscustomobject]@{ Distro = $Distro; Command = $Command; Text = $Text }
    }
    Set-ODSPortalWslConf 'Ubuntu Dev' @('user', 'default', 'maria', 'boot', 'systemd', 'true')
}
Check ($capturedConfig.Distro -ceq 'Ubuntu Dev' -and ($capturedConfig.Command -join '|') -ceq '/bin/sh|-s|--|/etc/wsl.conf|user|default|maria|boot|systemd|true') 'first-account configuration uses the fixed shell writer before Python exists'
Check ($capturedConfig.Text -match '^#!/bin/sh' -and $capturedConfig.Text -notmatch 'python3') 'shell writer is loaded from the installer bundle'
foreach ($settings in @(@('boot', 'systemd'), @('user', 'default', "maria`nroot"), @('boot', 'command', 'anything'))) {
    $rejected = $false
    try { Set-ODSPortalWslConf 'Ubuntu Dev' $settings } catch { $rejected = $true }
    Check $rejected 'configuration rejects unsupported or malformed settings before calling WSL'
}

# The resume script must re-run the same entry point with the same options,
# and hostile-looking values must stay literal strings.
$options = [ordered]@{ Distro='Ubuntu-24.04'; Voice=[switch]$true; NoLangfuse=$true; DryRun=$true; InstallDir="/home/o'brien/ods `$(x)"; Tier=''; Rag=$false }
$script = New-ODSPortalResumeScript "C:\Users\Ana Maria\AppData\Local\Temp\ods-install-1\ODS-main\install.ps1" $options
$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($script, [ref]$tokens, [ref]$errors)
Check ($errors.Count -eq 0) 'resume script parses'
$call = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.InvocationOperator -eq 'Ampersand' }, $true) | Select-Object -Last 1
Check ($call.CommandElements[0].Value -eq "C:\Users\Ana Maria\AppData\Local\Temp\ods-install-1\ODS-main\install.ps1") 'resume invokes the original entry script'
$text = $call.Extent.Text
Check ($text -match '-Distro ''Ubuntu-24.04''' -and $text -match '-Voice' -and $text -match '-NoLangfuse') 'resume keeps switches and values'
Check ($text -notmatch 'DryRun' -and $text -notmatch '-Rag' -and $text -notmatch '-Tier') 'resume drops dry-run, false switches and empty values'
$installDir = $call.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $_.Value -like '/home/*' }
Check ($installDir.Value -eq "/home/o'brien/ods `$(x)" -and $installDir.StringConstantType -eq 'SingleQuoted') 'resume values are single-quoted literals'

# Capacity gate: disk first, then virtualization only when WSL is not ready.
function Get-ODSPortalFreeSystemGB { return $script:free }
function Test-ODSPortalVirtualization { $script:virtChecked = $true; return $script:virt }
foreach ($case in @(
    @{ free=39; virt=$true; ready=$false; ok=$false; name='39 GB free is refused before installing WSL' },
    @{ free=20; virt=$true; ready=$true; ok=$true; name='low space only warns when WSL is already installed (rerun)' },
    @{ free=40; virt=$true; ready=$true; ok=$true; name='40 GB free passes' },
    @{ free=80; virt=$false; ready=$false; ok=$false; name='disabled virtualization is refused before WSL setup' },
    @{ free=80; virt=$false; ready=$true; ok=$true; name='working WSL does not depend on the firmware flag' })) {
    $script:free = $case.free; $script:virt = $case.virt; $script:virtChecked = $false
    $passed = $true
    try { Assert-ODSPortalHostCapacity $case.ready } catch { $passed = $false }
    Check ($passed -eq $case.ok) $case.name
}

# Docker inside the distro: a bounded wait that stops at the first answer
# or at the deadline, never beyond it.
$script:answers = [Collections.Generic.Queue[int]]::new()
function Invoke-ODSPortalWsl([string[]]$Arguments) { return [pscustomobject]@{ Code = $script:answers.Dequeue(); Output = ''; Error = '' } }
function Start-Sleep([int]$Seconds) { $script:slept += $Seconds }
$script:slept = 0; $script:answers.Enqueue(1); $script:answers.Enqueue(1); $script:answers.Enqueue(0)
Check ((Wait-ODSPortalDistroDocker 'Ubuntu-24.04' 600) -and $script:slept -eq 10) 'waits until docker answers inside the distro'
$script:answers.Clear(); $script:slept = 0; $script:answers.Enqueue(1)
Check (-not (Wait-ODSPortalDistroDocker 'Ubuntu-24.04' 0) -and $script:slept -eq 0) 'a zero-second wait checks once and gives up'
Remove-Item Function:\Start-Sleep

# Direct --exec does not load a login shell's sbin PATH. Exercise the real
# account helper with transports that cannot resolve either bare admin command.
$accountPassword = 'fixture password: "quoted"'
foreach ($mode in @('create', 'existing', 'create-fails', 'password-fails')) {
    $accountResult = & {
        param($Mode, $Password)
        $calls = [Collections.Generic.List[object]]::new()
        function Invoke-ODSPortalWsl([string[]]$Arguments) {
            $program = if ($Arguments[0] -eq '--terminate') { '--terminate' } else { $Arguments[5] }
            $calls.Add([pscustomobject]@{ Program = $program; Arguments = $Arguments; Text = $null })
            $code = 0
            if ($program -eq 'id') { $code = [int]($Mode -ne 'existing') }
            elseif ($program -eq 'useradd') { $code = 127 }
            elseif ($program -eq '/usr/sbin/useradd') { $code = [int]($Mode -eq 'create-fails') }
            elseif ($program -ne '--terminate') { throw "Unexpected mock command: $program" }
            return [pscustomobject]@{ Code = $code; Output = ''; Error = 'fixture command failed' }
        }
        function Invoke-ODSPortalWslInput([string]$Distro, [string[]]$Command, [string]$Text) {
            $calls.Add([pscustomobject]@{ Program = $Command[0]; Distro = $Distro; Arguments = $Command; Text = $Text })
            $code = 0
            if ($Command[0] -eq 'chpasswd') { $code = 127 }
            elseif ($Command[0] -eq '/usr/sbin/chpasswd') { $code = [int]($Mode -eq 'password-fails') }
            elseif ($Command[0] -ne '/bin/sh') { throw "Unexpected mock stdin command: $($Command[0])" }
            return [pscustomobject]@{ Code = $code; Output = '' }
        }
        $failure = ''
        try { New-ODSPortalLinuxAccount 'Ubuntu Dev' ([pscustomobject]@{ Name = 'maria'; Password = $Password }) }
        catch { $failure = $_.Exception.Message }
        [pscustomobject]@{ Calls = $calls.ToArray(); Failure = $failure }
    } $mode $accountPassword

    $expected = switch ($mode) {
        'create' { 'id|/usr/sbin/useradd|/usr/sbin/chpasswd|/bin/sh|--terminate' }
        'existing' { 'id|/usr/sbin/chpasswd|/bin/sh|--terminate' }
        'create-fails' { 'id|/usr/sbin/useradd' }
        'password-fails' { 'id|/usr/sbin/useradd|/usr/sbin/chpasswd' }
    }
    Check (($accountResult.Calls.Program -join '|') -ceq $expected) "$mode uses fixed admin paths and stops before later steps on failure"
    Check (($accountResult.Calls[0].Arguments -join '|') -ceq '--distribution|Ubuntu Dev|--user|root|--exec|id|-u|maria') "$mode checks the exact account as root in the selected distro"
    if ($mode -ne 'existing') {
        Check (($accountResult.Calls[1].Arguments -join '|') -ceq '--distribution|Ubuntu Dev|--user|root|--exec|/usr/sbin/useradd|--create-home|--shell|/bin/bash|--groups|sudo|--|maria') "$mode preserves separate useradd arguments without a UID override"
    }
    if ($mode -ne 'create-fails') {
        $passwordCall = $accountResult.Calls | Where-Object { $_.Program -eq '/usr/sbin/chpasswd' }
        Check ($passwordCall.Distro -ceq 'Ubuntu Dev' -and ($passwordCall.Arguments -join '|') -ceq '/usr/sbin/chpasswd' -and $passwordCall.Text -ceq ('maria:' + $accountPassword)) "$mode sends the password only through the fixed stdin command"
    }
    Check (-not (($accountResult.Calls | ForEach-Object { $_.Arguments -join '|' }) -join ' ').Contains($accountPassword)) "$mode never puts the password in command arguments"
    if ($mode -in @('create', 'existing')) {
        Check ($accountResult.Failure -ceq '' -and ($accountResult.Calls[-1].Arguments -join '|') -ceq '--terminate|Ubuntu Dev') "$mode restarts only the selected distro after configuration succeeds"
    } else {
        Check ($accountResult.Failure -match $(if ($mode -eq 'create-fails') { 'Could not create the Ubuntu user maria' } else { 'Could not set the Ubuntu password for maria' })) "$mode reports the failing account step"
    }
}

# A failed restart after writing the default user must stop setup: otherwise
# Ubuntu keeps opening as root and the user is sent to the wrong fix.
$failedRestart = & {
    function Invoke-ODSPortalWsl([string[]]$Arguments) { return [pscustomobject]@{ Code = $(if ($Arguments -contains '--terminate') { 1 } else { 0 }); Output = ''; Error = 'terminate failed' } }
    function Invoke-ODSPortalWslInput([string]$Distro, [string[]]$Command, [string]$Text) { return [pscustomobject]@{ Code = 0; Output = '' } }
    try { New-ODSPortalLinuxAccount 'Ubuntu-24.04' ([pscustomobject]@{ Name = 'maria'; Password = 'x' }); '' } catch { $_.Exception.Message }
}
Check ($failedRestart -match 'Could not restart Ubuntu-24.04' -and $failedRestart -match 'terminate failed') 'a failed distro restart after setting the default user stops setup'

# Password bytes reach the distro exactly: UTF-8, LF only, no console code page.
# Runnable only where a fake wsl.exe script can execute.
if ($IsLinux) {
    $fake = Join-Path ([IO.Path]::GetTempPath()) ('ods-fake-wsl-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $fake
    try {
        Set-Content -LiteralPath (Join-Path $fake 'wsl.exe') -Value "#!/bin/sh`nprintf '%s\n' `"`$*`" > `"`$(dirname `"`$0`")/args`"`ncat > `"`$(dirname `"`$0`")/stdin`"`nexit 0" -NoNewline
        chmod +x (Join-Path $fake 'wsl.exe')
        $previousPath = $env:PATH
        $env:PATH = $fake + [IO.Path]::PathSeparator + $env:PATH
        try { $result = Invoke-ODSPortalWslInput 'Ubuntu-24.04' @('/usr/sbin/chpasswd') 'maria:Senha çã:1 "x"' } finally { $env:PATH = $previousPath }
        $bytes = [IO.File]::ReadAllBytes((Join-Path $fake 'stdin'))
        Check ($result.Code -eq 0) 'stdin helper reports the command exit code'
        Check ([Text.Encoding]::UTF8.GetString($bytes) -ceq "maria:Senha çã:1 `"x`"`n") 'password is sent as UTF-8 with a single LF'
        Check (-not ($bytes -contains 13)) 'password stdin contains no carriage return'
        Check ((Get-Content -LiteralPath (Join-Path $fake 'args') -Raw).Trim() -eq '--distribution Ubuntu-24.04 --user root --exec /usr/sbin/chpasswd') 'password never appears in arguments'

        # Turning on systemd for an existing Ubuntu keeps its other wsl.conf settings.
        $env:PATH = $fake + [IO.Path]::PathSeparator + $env:PATH
        try { $null = Set-ODSPortalWslConf 'Ubuntu-24.04' @('boot', 'systemd', 'true') } finally { $env:PATH = $previousPath }
        Check ((Get-Content -LiteralPath (Join-Path $fake 'args') -Raw).Trim() -eq '--distribution Ubuntu-24.04 --user root --exec /bin/sh -s -- /etc/wsl.conf boot systemd true') 'wsl.conf writer runs as root without Python'
        $conf = Join-Path $fake 'wsl.conf'
        Set-Content -LiteralPath $conf -Value "[user]`ndefault=maria`n`n[network]`nhostname=pc`n" -NoNewline
        $writer = Get-Content -LiteralPath (Join-Path $fake 'stdin') -Raw
        $writer | /bin/sh -s -- $conf boot systemd true
        Check ($LASTEXITCODE -eq 0) 'wsl.conf writer succeeds on an existing file'
        $written = Get-Content -LiteralPath $conf -Raw
        Check ($written -match '(?m)^default=maria$' -and $written -match '(?m)^hostname=pc$' -and $written -match '(?ms)^\[boot\]\s+systemd = true') 'wsl.conf writer adds systemd and keeps existing settings'
    } finally { Remove-Item -LiteralPath $fake -Recurse -Force }
}

Write-Host "Passed $script:checks Windows Portal prerequisite contracts."
