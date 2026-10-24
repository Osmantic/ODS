# SPDX-License-Identifier: Apache-2.0
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../validate-cuda-runtime-pin.ps1')
$source=Join-Path $PSScriptRoot '../cuda-runtime-pin.json'
$nvidia=Join-Path $PSScriptRoot '../../../config/backends/nvidia.json'
$root=Join-Path ([IO.Path]::GetTempPath()) ('ods-cuda-pin-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $root
$checks=0
try {
    $null=Assert-CudaRuntimePinContract $source $nvidia
    $checks++; Write-Host 'PASS canonical CUDA metadata matches NVIDIA contract and reviewed complete file census'
    foreach($mutate in @(
        {param($p) $p.MinimumDriverVersion='100.00'},
        {param($p) $p.Archives[1].Sha256='a'*64},
        {param($p) $p.Archives[1].Size++},
        {param($p) $p.Archives[1].Url='https://example.invalid/engine.zip'},
        {param($p) $p.CudaArchsByCompute.'8.6'=@('999')},
        {param($p) $p.Files=$p.Files[1..($p.Files.Count-1)]},
        {param($p) $p.Files[0].Sha256='a'*64},
        {param($p) $p.Files[0].Size++},
        {param($p) $p.Files[1].Name=$p.Files[0].Name.ToUpperInvariant()},
        {param($p) $p.Files[0].Name='../escape.dll'},
        {param($p) $p.Files[0].Name='CON.dll'}
    )) {
        $pin=Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
        &$mutate $pin
        $path=Join-Path $root 'pin.json'
        [IO.File]::WriteAllText($path,($pin | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        $failed=$false
        try {$null=Assert-CudaRuntimePinContract $path $nvidia} catch {$failed=$true}
        if(-not $failed){throw 'Mutated CUDA metadata was accepted'}
        $checks++;Write-Host 'PASS changed archive/driver/compute/file census contract is refused'
    }
    Write-Host "Passed $checks CUDA metadata contracts."
} finally {
    if(-not ([IO.Path]::GetFullPath($root)).StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase)){throw 'Fixture cleanup escaped its temporary root.'}
    Remove-Item -LiteralPath $root -Recurse -Force
}
