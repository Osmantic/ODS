# SPDX-License-Identifier: Apache-2.0
# Execute version refusal before any package read, download or output mutation.
[CmdletBinding()]
param([string]$SourceDirectory)
$ErrorActionPreference='Stop'
if(-not $SourceDirectory){$SourceDirectory=Join-Path $PSScriptRoot '../desktop/windows'}
$builder=Join-Path $SourceDirectory 'build-installer.ps1'
$parseTokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($builder,[ref]$parseTokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'Installer builder did not parse.'}
$versionParameter=@($ast.ParamBlock.Parameters | Where-Object {$_.Name.VariablePath.UserPath -eq 'Version'})
if($versionParameter.Count -ne 1 -or $versionParameter[0].DefaultValue.SafeGetValue() -cne '0.2.0.0'){
    throw 'Document-capable packages require an installer version newer than the text-only preview.'
}
$iss=[IO.File]::ReadAllText((Join-Path $SourceDirectory 'installer.iss'))
if(-not $iss.Contains('#define PreviewVersion "0.2.0.0"')){throw 'Installer and public builder defaults disagree.'}
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ods-installer-version-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path (Join-Path $fixture 'package')
$package=Join-Path $fixture 'package'
$sentinel=Join-Path $package 'bundle.json'
[IO.File]::WriteAllText($sentinel,'{"fixture":"must remain unchanged"}',[Text.UTF8Encoding]::new($false))
$digest=(Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
$output=Join-Path $fixture 'output'
$cache=Join-Path $fixture 'tools'
$checks=1
try{
    foreach($version in @('0.0.0.0','0.1.0.0','0.1.65535.65535')){
        $message=''
        try{& $builder -PackageDirectory $package -OutputDirectory $output -ToolsCache $cache -Version $version}
        catch{$message=$_.Exception.Message}
        if($message -cne 'Saved document support requires preview installer version 0.2.0.0 or newer.'){
            throw "Version $version did not refuse at the document compatibility boundary: $message"
        }
        $checks++
    }
    foreach($version in @('0.2.0.0','0.10.0.0','1.0.0.0')){
        $message=''
        try{& $builder -PackageDirectory $package -OutputDirectory $output -ToolsCache $cache -Version $version}
        catch{$message=$_.Exception.Message}
        if($message -cne 'The desktop installer requires the source-built native CPU runtime and Portal profile.'){
            throw "Supported version $version did not reach the independent package boundary: $message"
        }
        $checks++
    }
    if((Test-Path -LiteralPath $output) -or (Test-Path -LiteralPath $cache) -or
        (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -cne $digest -or
        @(Get-ChildItem -LiteralPath $fixture -Recurse -File).Count -ne 1){
        throw 'Refused version/package validation changed fixture bytes or created output/tools.'
    }
    $checks++
    Write-Output "PASS $checks installer version/parity/numeric-order and pre-mutation refusal checks"
}finally{
    if((Split-Path $fixture -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\')){throw 'Unexpected version fixture path.'}
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
