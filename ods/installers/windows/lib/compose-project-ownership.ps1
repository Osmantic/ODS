# Guard the fixed `ods` Compose project and `ods-*` container names before an
# installer removes stale containers or launches Compose. Both are shared by
# Windows and WSL installations on the same Docker Desktop engine.

function Test-ODSComposeFullyQualifiedPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        return $false
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return $true
    }
    # Path.IsPathRooted accepts /ods on Windows, then GetFullPath turns it into
    # C:\ods. A WSL Compose label must never acquire ownership that way.
    return ($Path -match '^(?:[a-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))')
}

function Assert-ODSWindowsComposeContainerOwnership {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [string[]]$DockerClientArgs = @()
    )

    if (-not (Test-ODSComposeFullyQualifiedPath $InstallDir)) {
        throw 'ODS_INSTALL_ABORTED: Install directory must be absolute before inspecting Docker ownership.'
    }
    $expectedRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')

    try {
        # Project labels catch containers with custom names. The name scan also
        # covers legacy/unlabelled containers that phase 05 would otherwise rm.
        $projectIds = @(& docker @DockerClientArgs ps -a --no-trunc `
            --filter 'label=com.docker.compose.project=ods' --format '{{.ID}}' 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'Docker project enumeration failed.' }
        $namedIds = @(& docker @DockerClientArgs ps -a --no-trunc `
            --filter 'name=^ods-' --format '{{.ID}}' 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'Docker container-name enumeration failed.' }

        $ownedIds = @()
        foreach ($id in @($projectIds + $namedIds | Sort-Object -Unique)) {
            $id = ([string]$id).Trim()
            if ($id -notmatch '^[0-9a-fA-F]{12,64}$') {
                throw 'Docker returned an invalid container ID during ownership inspection.'
            }
            $raw = @(& docker @DockerClientArgs container inspect $id 2>$null)
            if ($LASTEXITCODE -ne 0 -or -not $raw.Count) {
                throw "Docker could not inspect ODS candidate container $id."
            }
            $items = @(($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
            if ($items.Count -ne 1 -or
                -not [string]::Equals([string]$items[0].Id, $id,
                    [StringComparison]::OrdinalIgnoreCase)) {
                throw "Docker returned inconsistent metadata for ODS candidate container $id."
            }
            $container = $items[0]
            $project = [string]$container.Config.Labels.'com.docker.compose.project'
            $workingDir = [string]$container.Config.Labels.'com.docker.compose.project.working_dir'
            $configFiles = [string]$container.Config.Labels.'com.docker.compose.project.config_files'
            $name = ([string]$container.Name).TrimStart('/')
            if ($project -ne 'ods' -and $name -notlike 'ods-*') { continue }
            if ($project -ne 'ods' -or [string]::IsNullOrWhiteSpace($workingDir) -or
                [string]::IsNullOrWhiteSpace($configFiles)) {
                throw "ODS container '$name' has missing or unexpected Compose ownership labels."
            }
            if (-not (Test-ODSComposeFullyQualifiedPath $workingDir)) {
                throw "ODS container '$name' belongs to '$workingDir', not '$InstallDir'."
            }
            $actualRoot = [IO.Path]::GetFullPath($workingDir).TrimEnd('\', '/')
            if (-not [string]::Equals($actualRoot, $expectedRoot,
                    [StringComparison]::OrdinalIgnoreCase)) {
                throw "ODS container '$name' belongs to '$workingDir', not '$InstallDir'."
            }
            # Compose separates absolute Windows paths with commas. A comma in
            # a profile or directory name is not a separator unless the next
            # segment begins another drive or UNC path.
            foreach ($file in [regex]::Split($configFiles, '(?i),(?=[a-z]:[\\/]|\\\\|/)')) {
                if (-not (Test-ODSComposeFullyQualifiedPath $file)) {
                    throw "ODS container '$name' has an unqualified Compose config path."
                }
                $actualFile = [IO.Path]::GetFullPath($file)
                if (-not $actualFile.StartsWith(
                        ($expectedRoot + [IO.Path]::DirectorySeparatorChar),
                        [StringComparison]::OrdinalIgnoreCase)) {
                    throw "ODS container '$name' uses a Compose config outside '$InstallDir'."
                }
            }
            if ($name -like 'ods-*') { $ownedIds += $id }
        }
        return $ownedIds
    } catch {
        Write-AIError "Cannot prove that Docker's 'ods' project and ods-* containers belong to $InstallDir."
        Write-AIError $_.Exception.Message
        Write-AIError 'No existing ODS containers will be removed or adopted. Inspect the other installation before retrying.'
        throw 'ODS_INSTALL_ABORTED'
    }
}

function Clear-ODSWindowsOwnedStaleContainers {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [string[]]$DockerClientArgs = @()
    )

    $ownedIds = @(Assert-ODSWindowsComposeContainerOwnership -InstallDir $InstallDir `
        -DockerClientArgs $DockerClientArgs)
    if ($ownedIds.Count -eq 0) { return 0 }

    # Inspect the exact IDs again before removal; names alone can be reused.
    $currentIds = @(Assert-ODSWindowsComposeContainerOwnership -InstallDir $InstallDir `
        -DockerClientArgs $DockerClientArgs)
    if (@(Compare-Object $ownedIds $currentIds).Count -ne 0) {
        Write-AIError 'ODS container ownership changed during the installer preflight.'
        throw 'ODS_INSTALL_ABORTED'
    }
    $null = & docker @DockerClientArgs rm -f @ownedIds 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-AIError "Could not remove this installation's stale ODS containers."
        throw 'ODS_INSTALL_ABORTED'
    }
    return $ownedIds.Count
}
