# Reviewed native runtime downloads. Dot-sourcing defines functions only.
function Resolve-ODSNativeLlamaArtifact {
    [CmdletBinding()]
    param([string]$ManifestPath, [string]$Platform, [string]$Tag)
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $null -eq $manifest.artifacts) {
        throw "Unsupported native llama artifact manifest."
    }
    $suffixes = @{ 'windows-vulkan-x64' = 'win-vulkan-x64.zip'; 'macos-arm64' = 'macos-arm64.tar.gz' }
    $seen = @{}
    $selected = $null
    foreach ($entry in $manifest.artifacts) {
        if (-not $suffixes.ContainsKey([string]$entry.platform) -or [string]$entry.tag -cnotmatch '\Ab[0-9]+\z') {
            throw "Unsupported native llama artifact selection."
        }
        $asset = "llama-$($entry.tag)-bin-$($suffixes[[string]$entry.platform])"
        $url = "https://github.com/ggml-org/llama.cpp/releases/download/$($entry.tag)/$asset"
        if ($entry.asset -cne $asset -or $entry.url -cne $url) {
            throw "Native llama artifact URL/asset does not match its selection."
        }
        if ([string]$entry.sha256 -cnotmatch '\A[0-9a-f]{64}\z') {
            throw "Native llama artifact has no valid reviewed SHA-256."
        }
        $key = "$($entry.platform)/$($entry.tag)"
        if ($seen.ContainsKey($key)) { throw "Duplicate native llama artifact selection." }
        $seen[$key] = $true
        if ($entry.platform -ceq $Platform -and $entry.tag -ceq $Tag) { $selected = $entry }
    }
    if ($null -eq $selected) { throw "No reviewed native llama artifact for $Platform/$Tag." }
    return $selected
}

function Test-ODSNativeLlamaArchive {
    [CmdletBinding()]
    [OutputType([bool])]
    param([string]$Path, [string]$ExpectedSha256)
    if ($ExpectedSha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw "No valid reviewed native llama SHA-256." }
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.PSIsContainer -or $item.Length -le 0 -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "Native llama archive is not a nonempty regular staged file."
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash
    if ($actual -ine $ExpectedSha256) { throw "Native llama archive SHA-256 does not match the reviewed artifact." }
    return $true
}

function Install-ODSVerifiedNativeLlama {
    [CmdletBinding()]
    param([string]$SourceRoot, [string]$Tag, [string]$InstallDir)
    $artifact = Resolve-ODSNativeLlamaArtifact -ManifestPath (Join-Path $SourceRoot 'installers/native-llama-artifacts.json') `
        -Platform 'windows-vulkan-x64' -Tag $Tag
    $installRoot = [System.IO.Path]::GetFullPath($InstallDir)
    $destination = [System.IO.Path]::GetFullPath((Join-Path $installRoot 'llama-server'))
    # This is the dedicated existing destination owned by this installer, not an
    # arbitrary path from the downloaded archive or its metadata.
    if ([System.IO.Path]::GetDirectoryName($destination) -ine $installRoot.TrimEnd('\', '/')) {
        throw "Native llama destination escaped its installation root."
    }
    if ((Test-Path -LiteralPath $destination) -and
        ((Get-Item -LiteralPath $destination).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "Native llama destination is a reparse point."
    }
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $stage = Join-Path $temporaryRoot ('ods-native-llama-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $stage -ErrorAction Stop
    try {
        # A fresh random directory with a current-user-only ACL protects the
        # verified bytes from other users until extraction has completed.
        $security = New-Object System.Security.AccessControl.DirectorySecurity
        $security.SetAccessRuleProtection($true, $false)
        $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.SetOwner($user)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $user, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'
        )
        $security.AddAccessRule($rule)
        Set-Acl -LiteralPath $stage -AclObject $security -ErrorAction Stop
        $archive = Join-Path $stage ([string]$artifact.asset)
        if (-not (Invoke-DownloadWithRetry -Url ([string]$artifact.url) -Destination $archive -Label 'Downloading llama-server (Vulkan)')) {
            throw "Native llama archive download failed."
        }
        $null = Test-ODSNativeLlamaArchive -Path $archive -ExpectedSha256 ([string]$artifact.sha256)
        # Parsing/extraction is unreachable for unverified bytes.
        if (-not (Invoke-ExtractionWithRetry -ZipPath $archive -DestinationPath $destination)) {
            throw "Verified native llama archive extraction failed."
        }
        $found = Get-ChildItem -LiteralPath $destination -Recurse -Filter 'llama-server.exe' -File | Select-Object -First 1
        if ($null -eq $found) { throw "llama-server.exe not found in the verified archive." }
        if ($found.DirectoryName -ine $destination) {
            foreach ($item in Get-ChildItem -LiteralPath $found.DirectoryName -Force) {
                $source = [System.IO.Path]::GetFullPath($item.FullName)
                $target = [System.IO.Path]::GetFullPath((Join-Path $destination $item.Name))
                $boundary = $destination.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
                if (-not $source.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase) -or
                    [System.IO.Path]::GetDirectoryName($target) -ine $destination -or
                    ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                    throw "Native llama archive member escaped its destination."
                }
                Move-Item -LiteralPath $source -Destination $target -Force -ErrorAction Stop
            }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $destination 'llama-server.exe') -PathType Leaf)) {
            throw "llama-server.exe was not installed from the verified archive."
        }
    } finally {
        $cleanup = [System.IO.Path]::GetFullPath($stage)
        if ([System.IO.Path]::GetDirectoryName($cleanup) -ine $temporaryRoot -or
            [System.IO.Path]::GetFileName($cleanup) -cnotmatch '\Aods-native-llama-[0-9a-f]{32}\z') {
            throw "Refusing native llama staging cleanup outside the temporary root."
        }
        if (Test-Path -LiteralPath $cleanup) {
            if ((Get-Item -LiteralPath $cleanup).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Refusing recursive native llama cleanup of a reparse point."
            }
            Remove-Item -LiteralPath $cleanup -Recurse -Force -ErrorAction Stop
        }
    }
}
