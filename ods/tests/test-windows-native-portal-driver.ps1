# SPDX-License-Identifier: Apache-2.0
# Compile pure console fixtures against harmless assembly references, then run
# an exact private copy of the actual Portal driver. No GUI, model or network.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$driver=Join-Path $PSScriptRoot 'test-windows-native-portal.ps1'
$body=[IO.File]::ReadAllText($driver)
$sourceHash=(Get-FileHash -LiteralPath $driver).Hash
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-driver-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
$interpreter=(Get-Process -Id $PID).Path
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$encoding=[Text.UTF8Encoding]::new($false)
$checks=0
function Assert-PortalDriver([bool]$Condition,[string]$Message){
    if(-not $Condition){throw $Message}
    $script:checks++;Write-Output ('PASS '+$Message)
}
function Test-ExactUtf8([string]$Path,[string]$Expected){
    return (Test-Path -LiteralPath $Path) -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path)) -ceq
        [Convert]::ToBase64String($encoding.GetBytes($Expected))
}
try {
    $bundle=Join-Path $fixture 'harmless bundle'
    $null=New-Item -ItemType Directory -Path $bundle
    $reference=Join-Path $fixture 'Reference.cs'
    $names=@('ODS Windows Preview.exe','OdsPortal.dll','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.Wpf.dll')
    for($index=0;$index -lt $names.Count;$index++){
        [IO.File]::WriteAllText($reference,('public static class Reference'+$index+' { public static int Main(){return 0;} }'),$encoding)
        & $compiler /nologo /target:library ("/out:"+(Join-Path $bundle $names[$index])) $reference
        if($LASTEXITCODE -ne 0){throw 'Harmless driver reference assembly did not compile.'}
    }
    $tests=Join-Path $fixture 'copy/ods/desktop/windows/tests'
    $scripts=Join-Path $fixture 'copy/ods/tests'
    $null=New-Item -ItemType Directory -Path $tests,$scripts
    $copiedDriver=Join-Path $scripts 'test-windows-native-portal.ps1'
    [IO.File]::WriteAllText($copiedDriver,$body,$encoding)
    foreach($name in @('NativePortalTests.cs','DocumentSaveDialogTests.cs')){
        [IO.File]::WriteAllText((Join-Path $tests $name),'// Deliberately no UI fixture code.',$encoding)
    }
    $consoleSource=@'
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
static class PortalUiTests
{
    static int Main(string[] args)
    {
        // A broken pipe-drain implementation must not orphan this console child.
        new Thread(() => { Thread.Sleep(10000); Environment.Exit(91); }) { IsBackground = true }.Start();
        var utf8 = new UTF8Encoding(false); Console.OutputEncoding = utf8;
        Console.SetError(new StreamWriter(Console.OpenStandardError(), utf8) { AutoFlush = true });
        File.WriteAllText(Environment.GetEnvironmentVariable("ODS_DRIVER_OBSERVED"),
            new JavaScriptSerializer().Serialize(new { arguments = args, cwd = Environment.CurrentDirectory }), utf8);
        string mode = Environment.GetEnvironmentVariable("ODS_DRIVER_CASE");
        if (mode == "empty") return 0;
        if (mode == "startup") { Console.Write("startup contract stdout"); return 0; }
        if (mode == "empty-screenshot") { Console.Error.Write("short UTF8 \u1ec7 \u1ef1"); return 0; }
        Task.WaitAll(Task.Run(() => Console.Write(new string('o', 131072) + " stdout UTF8 \u1ec7 \u1ef1")),
            Task.Run(() => Console.Error.Write(new string('e', 131072) + " stderr UTF8 \u1ec7 \u1ef1")));
        return 17;
    }
}
'@
    [IO.File]::WriteAllText((Join-Path $tests 'PortalUiTests.cs'),$consoleSource,$encoding)
    $tag=' UTF8 '+[char]0x1EC7+' '+[char]0x1EF1
    foreach($caseName in @('empty','failure','startup','empty-screenshot','lost-stderr-mutant','exclusive-startup')){
        $directory=Join-Path $fixture $caseName
        $null=New-Item -ItemType Directory -Path $directory
        $diagnostics=Join-Path $directory 'persistent diagnostics'
        $observed=Join-Path $directory 'observed.json'
        $state=Join-Path $directory ('state '+[char]0x0111+' with spaces\')
        $screenshot=Join-Path $directory ('screen '+[char]0x1EEF+' with spaces.png')
        $invoke=@('-Bundle',$bundle,'-DiagnosticsDirectory',$diagnostics)
        $expectedArguments=@()
        if($caseName -in @('failure','lost-stderr-mutant')){
            $invoke+=@('-LiveState',$state,'-Screenshot',$screenshot)
            $expectedArguments=@('--live',[IO.Path]::GetFullPath($state),[IO.Path]::GetFullPath($screenshot))
        }elseif($caseName -eq 'empty-screenshot'){
            $invoke+=@('-LiveState',$state)
            $expectedArguments=@('--live',[IO.Path]::GetFullPath($state),'')
        }elseif($caseName -eq 'startup'){$invoke+='-StartupContracts';$expectedArguments=@('--startup-contracts')}
        elseif($caseName -eq 'exclusive-startup'){$invoke+=@('-StartupContracts','-DocumentFixture')}
        if($caseName -eq 'lost-stderr-mutant'){
            $boundary='[IO.File]::WriteAllText($stderrPath,$stderrText,$encoding)'
            if(($body.Split(@($boundary),[StringSplitOptions]::None).Count-1) -ne 1){throw 'Review the lost-stderr negative-control boundary.'}
            [IO.File]::WriteAllText($copiedDriver,$body.Replace($boundary,'[IO.File]::WriteAllText($stderrPath,'''',$encoding)'),$encoding)
        }else{[IO.File]::WriteAllText($copiedDriver,$body,$encoding)}
        $runner=Join-Path $directory 'invoke.ps1'
        $quoted=@($invoke | ForEach-Object {if($_.StartsWith('-')){$_}else{"'"+$_.Replace("'","''")+"'"}}) -join ' '
        # Windows PowerShell must recognize the generated non-ASCII path literals.
        [IO.File]::WriteAllText($runner,("& '"+$copiedDriver.Replace("'","''")+"' "+$quoted),[Text.UTF8Encoding]::new($true))
        $start=[Diagnostics.ProcessStartInfo]::new()
        $start.FileName=$interpreter;$start.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$runner+'"'
        $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.WorkingDirectory=$directory
        $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        $start.StandardOutputEncoding=$encoding;$start.StandardErrorEncoding=$encoding
        $start.EnvironmentVariables['ODS_DRIVER_CASE']=$(if($caseName -eq 'lost-stderr-mutant'){'failure'}else{$caseName})
        $start.EnvironmentVariables['ODS_DRIVER_OBSERVED']=$observed
        $child=[Diagnostics.Process]::new();$child.StartInfo=$start
        try {
            if(-not $child.Start()){throw 'The isolated Portal driver child did not start.'}
            $stdout=$child.StandardOutput.ReadToEndAsync();$stderr=$child.StandardError.ReadToEndAsync()
            if(-not $child.WaitForExit(20000)){$child.Kill();$null=$child.WaitForExit(5000);throw 'The isolated Portal driver exceeded its 20-second deadline.'}
            if(-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),5000)){throw 'Driver fixture output did not drain.'}
            $console=$stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult();$exitCode=$child.ExitCode
        }finally{$child.Dispose()}
        if($caseName -eq 'exclusive-startup'){
            Assert-PortalDriver ($exitCode -ne 0 -and $console.Contains('StartupContracts cannot be combined') -and
                -not (Test-Path -LiteralPath $observed) -and -not (Test-Path -LiteralPath $diagnostics)) 'startup contracts reject another mode before compiling or invoking the runner'
            continue
        }
        if(-not (Test-Path -LiteralPath (Join-Path $diagnostics 'runner.json'))){throw "Driver $caseName did not persist its exit evidence. $console"}
        $record=[IO.File]::ReadAllText((Join-Path $diagnostics 'runner.json'),$encoding) | ConvertFrom-Json
        $actual=[IO.File]::ReadAllText($observed,$encoding) | ConvertFrom-Json
        if($actual.cwd -ne $record.working_directory -or (Test-Path -LiteralPath $record.working_directory) -or
            -not $console.Contains('PASS private picker default-name sentinel remains unchanged') -or
            @($actual.arguments).Count -ne $expectedArguments.Count){throw "Driver $caseName lost argv, cwd, cleanup or sentinel custody. $console"}
        for($index=0;$index -lt $expectedArguments.Count;$index++){
            if($actual.arguments[$index] -cne $expectedArguments[$index]){throw "Driver $caseName changed argument $index."}
        }
        $stdoutPath=Join-Path $diagnostics 'stdout.log';$stderrPath=Join-Path $diagnostics 'stderr.log'
        switch($caseName){
            'empty' {Assert-PortalDriver ($exitCode -eq 0 -and $record.exit_code -eq 0 -and (Test-ExactUtf8 $stdoutPath '') -and
                (Test-ExactUtf8 $stderrPath '')) 'successful empty stdout/stderr and exit0 remain durable after the private fixture is deleted'}
            'failure' {Assert-PortalDriver ($exitCode -ne 0 -and $record.exit_code -eq 17 -and $console.Contains('failed (exit 17)') -and
                (Test-ExactUtf8 $stdoutPath (('o'*131072)+' stdout'+$tag)) -and (Test-ExactUtf8 $stderrPath (('e'*131072)+' stderr'+$tag))) 'simultaneous large UTF8 stdout/stderr and exact exit17 survive failure with Unicode, spaced and trailing-backslash argv'}
            'startup' {Assert-PortalDriver ($exitCode -eq 0 -and $record.exit_code -eq 0 -and (Test-ExactUtf8 $stdoutPath 'startup contract stdout') -and
                (Test-ExactUtf8 $stderrPath '')) 'StartupContracts reaches the actual runner command with durable stdout and empty stderr'}
            'empty-screenshot' {Assert-PortalDriver ($exitCode -eq 0 -and $record.exit_code -eq 0 -and (Test-ExactUtf8 $stdoutPath '') -and
                (Test-ExactUtf8 $stderrPath ('short'+$tag))) 'an explicit empty screenshot argument and nonempty UTF8 stderr survive a successful runner'}
            'lost-stderr-mutant' {Assert-PortalDriver ($exitCode -ne 0 -and $record.exit_code -eq 17 -and
                (Test-ExactUtf8 $stdoutPath (('o'*131072)+' stdout'+$tag)) -and (Test-ExactUtf8 $stderrPath '') -and
                -not (Test-ExactUtf8 $stderrPath (('e'*131072)+' stderr'+$tag))) 'private lost-stderr mutant fails the exact persisted-error contract despite preserving stdout and exit17'}
        }
    }
    if((Get-FileHash -LiteralPath $driver).Hash -cne $sourceHash){throw 'The actual Portal driver changed during its contract.'}
    Write-Output "PASS $checks actual Portal driver diagnostics cases on PowerShell $($PSVersionTable.PSVersion)"
}finally{
    if([IO.Path]::GetDirectoryName($fixture) -ne [IO.Path]::GetTempPath().TrimEnd('\')){throw 'Unexpected Portal driver fixture path.'}
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
