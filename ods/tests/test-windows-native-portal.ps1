# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Bundle, [string]$LiveState, [string]$Screenshot,
    [string]$FixtureState, [switch]$VerifySaved, [switch]$DocumentFixture, [switch]$DocumentLive,
    [string]$DiagnosticsDirectory, [switch]$StartupContracts)
$ErrorActionPreference='Stop'
if($StartupContracts -and ($LiveState -or $Screenshot -or $FixtureState -or $VerifySaved -or $DocumentFixture -or $DocumentLive)){
    throw 'StartupContracts cannot be combined with another Portal test mode or state/screenshot options.'
}
if (($DocumentFixture -and ($LiveState -or $VerifySaved -or $DocumentLive)) -or
    ($DocumentLive -and (-not $LiveState -or $FixtureState -or $VerifySaved))) {
    throw 'DocumentFixture accepts only an optional FixtureState; DocumentLive requires LiveState and cannot verify a fixture.'
}
$livePath=if($LiveState){[IO.Path]::GetFullPath($LiveState)}else{''}
$fixtureStatePath=if($FixtureState){[IO.Path]::GetFullPath($FixtureState)}else{''}
$screenshotPath=if($Screenshot){[IO.Path]::GetFullPath($Screenshot)}else{''}
$source=Join-Path $PSScriptRoot '../desktop/windows'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-ui-'+[guid]::NewGuid().ToString('N'))
$diagnostics=if($DiagnosticsDirectory){[IO.Path]::GetFullPath($DiagnosticsDirectory)}else{''}
if($diagnostics){
    if($diagnostics -eq $fixture -or $diagnostics.StartsWith($fixture+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
        throw 'Runner diagnostics must be outside the disposable Portal fixture.'
    }
    if(Test-Path -LiteralPath $diagnostics){
        if(@(Get-ChildItem -LiteralPath $diagnostics -Force).Count){throw 'Use an empty diagnostics directory to preserve prior evidence.'}
    }else{$null=New-Item -ItemType Directory -Path $diagnostics}
}
function ConvertTo-PortalArgument([string]$Value){
    # Windows argv quoting: protect embedded quotes and trailing backslashes,
    # including an explicit empty screenshot argument.
    $escaped=[regex]::Replace($Value,'(\\*)"','$1$1\"')
    $escaped=[regex]::Replace($escaped,'(\\+)$','$1$1')
    return '"'+$escaped+'"'
}
$null=New-Item -ItemType Directory -Path $fixture
try {
    Get-ChildItem -LiteralPath $Bundle | Where-Object Name -NE 'models' | Copy-Item -Destination $fixture -Recurse
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    $wpf=Join-Path (Split-Path $compiler) 'WPF'
    $runner=Join-Path $fixture 'PortalUiTests.exe'
    & $compiler /nologo /target:exe /platform:x64 "/out:$runner" `
        "/reference:$fixture/ODS Windows Preview.exe" "/reference:$fixture/OdsPortal.dll" `
        "/reference:$fixture/Microsoft.Web.WebView2.Core.dll" "/reference:$fixture/Microsoft.Web.WebView2.Wpf.dll" `
        /reference:System.Web.Extensions.dll /reference:System.Xaml.dll "/reference:$wpf/WindowsBase.dll" `
        "/reference:$wpf/UIAutomationClient.dll" "/reference:$wpf/UIAutomationTypes.dll" `
        "/reference:$wpf/PresentationFramework.dll" "/reference:$wpf/PresentationCore.dll" `
        (Join-Path $source 'tests/PortalUiTests.cs') (Join-Path $source 'tests/NativePortalTests.cs') `
        (Join-Path $source 'tests/DocumentSaveDialogTests.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Native Portal UI tests did not compile.' }
    # A native picker with an uncommitted filename must never default to the
    # source checkout. Preserve a sentinel even on a failing graphical test.
    $defaultFile=Join-Path $fixture 'document.md'
    [IO.File]::WriteAllText($defaultFile,'Preserve this default-name fixture sentinel.',[Text.UTF8Encoding]::new($false))
    $defaultHash=(Get-FileHash -LiteralPath $defaultFile -Algorithm SHA256).Hash
    Push-Location $fixture
    try {
        $runnerArguments=@()
        if($StartupContracts){$runnerArguments=@('--startup-contracts')}
        elseif ($DocumentLive) { $runnerArguments=@('--document-live',$livePath,$screenshotPath) }
        elseif ($DocumentFixture) {
            if ($FixtureState) { $runnerArguments=@('--document-fixture',$fixtureStatePath) }
            else { $runnerArguments=@('--document-fixture') }
        }
        elseif ($LiveState) { $runnerArguments=@('--live',$livePath,$screenshotPath) }
        elseif ($FixtureState) { $runnerArguments=@($(if($VerifySaved){'--verify-state'}else{'--saved-state'}),$fixtureStatePath) }
        $encoding=[Text.UTF8Encoding]::new($false)
        $start=[Diagnostics.ProcessStartInfo]::new()
        $start.FileName=$runner;$start.WorkingDirectory=$fixture
        $start.Arguments=($runnerArguments | ForEach-Object {ConvertTo-PortalArgument $_}) -join ' '
        $start.UseShellExecute=$false;$start.CreateNoWindow=$true
        $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        $start.StandardOutputEncoding=$encoding;$start.StandardErrorEncoding=$encoding
        $child=[Diagnostics.Process]::new();$child.StartInfo=$start
        $started=[DateTime]::UtcNow.ToString('o')
        try {
            if(-not $child.Start()){throw 'The native Portal runner did not start.'}
            # Start both reads before waiting; either pipe can exceed its buffer.
            $stdout=$child.StandardOutput.ReadToEndAsync();$stderr=$child.StandardError.ReadToEndAsync()
            $child.WaitForExit()
            $stdoutText=$stdout.GetAwaiter().GetResult();$stderrText=$stderr.GetAwaiter().GetResult()
            $testExit=$child.ExitCode
            if($diagnostics){
                $stdoutPath=Join-Path $diagnostics 'stdout.log';$stderrPath=Join-Path $diagnostics 'stderr.log'
                [IO.File]::WriteAllText($stdoutPath,$stdoutText,$encoding)
                [IO.File]::WriteAllText($stderrPath,$stderrText,$encoding)
                $record=@{schemaVersion=1;started_utc=$started;finished_utc=[DateTime]::UtcNow.ToString('o');
                    executable=$runner;working_directory=$fixture;arguments=$runnerArguments;process_id=$child.Id;
                    exit_code=$testExit;encoding='UTF-8';stdout='stdout.log';stderr='stderr.log'}
                [IO.File]::WriteAllText((Join-Path $diagnostics 'runner.json'),($record|ConvertTo-Json -Depth 4),$encoding)
            }
            # Success-stream output is retained by Start-Transcript as well as
            # Actions. Persist the original stderr separately before reporting.
            if($stdoutText){Write-Output $stdoutText}
            if($stderrText){Write-Output 'Portal runner stderr:';Write-Output $stderrText}
        } finally {$child.Dispose()}
    } finally {
        Pop-Location
        if(-not (Test-Path -LiteralPath $defaultFile) -or (Get-FileHash -LiteralPath $defaultFile -Algorithm SHA256).Hash -ne $defaultHash) {
            throw 'The graphical test changed its private default-name sentinel.'
        }
        Write-Output 'PASS private picker default-name sentinel remains unchanged'
    }
    if ($testExit -ne 0) { throw "Native Portal UI tests failed (exit $testExit)." }
} finally {
    if ((Split-Path $fixture -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\')) { throw 'Unexpected UI fixture path.' }
    # WebView2 releases its browser metrics handle shortly after the test
    # process exits. Bound that observed shutdown delay; never kill a browser.
    $shutdown=[Diagnostics.Stopwatch]::StartNew()
    while (Test-Path -LiteralPath $fixture) {
        try { Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction Stop }
        catch [IO.IOException] { if ($shutdown.Elapsed.TotalSeconds -ge 15) { throw }; Start-Sleep -Milliseconds 100 }
        catch [UnauthorizedAccessException] { if ($shutdown.Elapsed.TotalSeconds -ge 15) { throw }; Start-Sleep -Milliseconds 100 }
    }
}
