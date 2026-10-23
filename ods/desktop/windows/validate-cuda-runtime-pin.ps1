# SPDX-License-Identifier: Apache-2.0
param(
    [string]$PinPath=(Join-Path $PSScriptRoot 'cuda-runtime-pin.json'),
    [string]$NvidiaPath=(Join-Path $PSScriptRoot '../../config/backends/nvidia.json')
)
$ErrorActionPreference='Stop'

function Assert-CudaRuntimePinContract([string]$MetadataPath,[string]$BackendPath) {
    $pin=Get-Content -LiteralPath $MetadataPath -Raw | ConvertFrom-Json
    $native=(Get-Content -LiteralPath $BackendPath -Raw | ConvertFrom-Json).runtime.llama_server.windows_x64
    if(-not $native -or $native.backend -cne 'cuda' -or $pin.ReleaseTag -cne $native.release_tag -or
        $pin.Flavor -cne $native.flavor -or $pin.MinimumDriverVersion -cne $native.min_driver_version -or
        @($pin.Archives).Count -ne 2 -or @($native.archives).Count -ne 2) {throw 'CUDA metadata differs from the Windows NVIDIA runtime contract.'}
    $archiveNames=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $archiveHashes=@()
    for($i=0;$i -lt 2;$i++) {
        $a=$pin.Archives[$i];$b=$native.archives[$i]
        $expected="https://github.com/ggml-org/llama.cpp/releases/download/$($native.release_tag)/$($b.asset)"
        if($a.File -cne $b.asset -or $a.Sha256 -cne $b.sha256 -or $a.Size -ne $b.size -or
            ($a.Size -isnot [int] -and $a.Size -isnot [long]) -or $a.Url -cne $expected -or -not $archiveNames.Add([string]$a.File)) {
            throw 'CUDA archive metadata differs from the pinned NVIDIA archive identity.'
        }
        $archiveHashes += [string]$a.Sha256
    }
    $keys=@($pin.CudaArchsByCompute.PSObject.Properties.Name | Sort-Object)
    $expectedKeys=@($native.cuda_archs_by_compute.PSObject.Properties.Name | Sort-Object)
    if(($keys -join ',') -cne ($expectedKeys -join ',')){throw 'CUDA compute-capability keys differ from the NVIDIA contract.'}
    foreach($key in $keys) {
        if((@($pin.CudaArchsByCompute.$key) -join ',') -cne (@($native.cuda_archs_by_compute.$key) -join ',')) {
            throw 'CUDA compiled architecture aliases differ from the NVIDIA contract.'
        }
    }
    $names=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $byName=@{}
    if(@($pin.Files).Count -lt 2 -or @($pin.Files).Count -gt 256){throw 'Invalid CUDA file census.'}
    foreach($file in $pin.Files) {
        $name=[string]$file.Name
        if($name.Length -gt 180 -or $name -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9._-]*\z' -or $name.EndsWith('.') -or
            $name -match '\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)' -or -not $names.Add($name) -or
            [string]$file.Sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or ($file.Size -isnot [int] -and $file.Size -isnot [long]) -or
            $file.Size -le 0 -or $file.Size -gt 2147483648L){throw 'Invalid CUDA Windows filename, size or SHA-256 census.'}
        $byName[$name]=$file
    }
    foreach($name in $native.required_files){if(-not $names.Contains([string]$name)){throw "CUDA file census omits $name."}}
    # This digest binds every name, length and SHA, including files outside
    # nvidia.json's minimum DLL set, to the reviewed two-archive census. A pin
    # upgrade requires independently re-verifying both ZIPs and this census.
    $reviewedPair='bef10c5cca99c6c53a4f996fc60763237f68c3ba4f5b0de6a12c870d30c70441|8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6'
    if(($archiveHashes -join '|') -cne $reviewedPair){throw 'The CUDA archive pair needs a new reviewed complete file census.'}
    [string[]]$orderedNames=@($byName.Keys);[Array]::Sort($orderedNames,[StringComparer]::Ordinal)
    $text=[Text.StringBuilder]::new()
    foreach($name in $orderedNames){$f=$byName[$name];$null=$text.Append($name).Append(':').Append([long]$f.Size).Append(':').Append([string]$f.Sha256).Append("`n")}
    $hash=[Security.Cryptography.SHA256]::Create()
    try {$digest=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($text.ToString())))).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
    if($digest -cne '45178de1715d3c8d48566f26d11196128e7ba090574a62c2c651e0ce24643081'){throw 'The CUDA file census differs from the reviewed archive contents.'}
    return $pin
}

if($MyInvocation.InvocationName -ne '.') {
    $null=Assert-CudaRuntimePinContract $PinPath $NvidiaPath
    Write-Output 'CUDA runtime metadata matches the NVIDIA contract and reviewed complete file census.'
}
