param(
    [string]$Version = "1.0.1",
    [switch]$Force,
    [switch]$Upload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $RootDir

if ([string]::IsNullOrWhiteSpace($Version) -or $Version -match '[\\/]') {
    Write-Host "Error: Version must be a non-empty release name without path separators." -ForegroundColor Red
    exit 1
}

$StagingFolder = "GMF-CMP-Monitor-Portable"
$StagingParent = Join-Path $RootDir "staging"
$StagingDir = Join-Path $StagingParent $StagingFolder
$ReleaseDir = Join-Path $RootDir "release"
$ZipName = "GMF-CMP-Monitor-v$Version-portable.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName

$AllowedFiles = @(
    "setup.bat",
    "start_monitor.bat",
    "requirements.txt",
    ".env.example",
    "README.md",
    "main.py",
    "cmp_auth.py",
    "dashboard_monitor.py",
    "imap_client.py",
    "network_diag.py",
    "network_probe.py",
    "otp.py",
    "vpn.py",
    "config.py"
)

$ForbiddenPatterns = @(
    "\.env$",
    "\.log$",
    "logs",
    "\.git",
    "__pycache__",
    "\.pyc$",
    "tests",
    "\.pytest_cache"
)

function Stop-Packaging {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Write-Host "Error: $Message" -ForegroundColor Red
    if (Test-Path -LiteralPath $StagingDir) {
        Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

function Test-ForbiddenName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $ExactNamePatterns = @("logs", "__pycache__", "tests", ".pytest_cache")
    foreach ($Pattern in $ForbiddenPatterns) {
        if ($ExactNamePatterns -contains $Pattern) {
            if ($Name -eq $Pattern) {
                return $true
            }
        } elseif ($Name -match $Pattern) {
            return $true
        }
    }
    return $false
}

if (Test-Path -LiteralPath $StagingDir) {
    if ($Force) {
        Write-Host "Force specified: removing existing staging directory." -ForegroundColor Yellow
        Remove-Item -LiteralPath $StagingDir -Recurse -Force
    } elseif (@(Get-ChildItem -LiteralPath $StagingDir -Force).Count -gt 0) {
        Stop-Packaging "Staging directory already contains files. Use -Force to replace it: $StagingDir"
    }
}

if (Test-Path -LiteralPath $ZipPath) {
    if ($Force) {
        Write-Host "Force specified: removing existing release archive." -ForegroundColor Yellow
        Remove-Item -LiteralPath $ZipPath -Force
    } else {
        Stop-Packaging "Release archive already exists. Use -Force to replace it: $ZipPath"
    }
}

if (-not (Test-Path -LiteralPath $StagingParent)) {
    New-Item -ItemType Directory -Path $StagingParent | Out-Null
}
if (-not (Test-Path -LiteralPath $StagingDir)) {
    New-Item -ItemType Directory -Path $StagingDir | Out-Null
}
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
}

$DistDir = Join-Path $RootDir "dist\GMF-CMP-Monitor"
$ExePath = Join-Path $DistDir "GMF-CMP-Monitor.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    Stop-Packaging "Cannot find $ExePath. Run .\scripts\build_exe.ps1 first."
}
Write-Host "Copying frozen distribution from $DistDir..." -ForegroundColor Cyan
$DistEntries = @(
    Get-ChildItem -LiteralPath $DistDir -Force
)
Write-Host "Scanned $($DistEntries.Count) entries in $DistDir" -ForegroundColor Gray
foreach ($Entry in $DistEntries) {
    Copy-Item -LiteralPath $Entry.FullName -Destination $StagingDir -Recurse -Force
}

if (-not (Test-Path (Join-Path $StagingDir "browsers"))) {
    Stop-Packaging "Bundled browsers directory is missing from staging."
}
Write-Host "Staging contents after copy:" -ForegroundColor Gray
Get-ChildItem -LiteralPath $StagingDir -Force | Select-Object Name, PSIsContainer | ForEach-Object { Write-Host "  $($_.Name) container=$($_.PSIsContainer)" }

$BrowserDir = Join-Path $StagingDir "browsers"
$FirefoxExe = Get-ChildItem -LiteralPath $BrowserDir -Recurse -File -Filter "firefox.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $FirefoxExe) {
    Stop-Packaging "Bundled Firefox is missing under $BrowserDir. Run .\scripts\build_exe.ps1 first."
}
Write-Host "Bundled Firefox verified at $($FirefoxExe.FullName)" -ForegroundColor Green

Write-Host "Populating clean staging directory from the explicit allowlist..." -ForegroundColor Cyan
$MissingFiles = @()
foreach ($RelativePath in $AllowedFiles) {
    $SourcePath = Join-Path $RootDir $RelativePath
    $DestinationPath = Join-Path $StagingDir $RelativePath

    if (-not (Test-Path -LiteralPath $SourcePath)) {
        $MissingFiles += $RelativePath
        continue
    }

    $DestinationDirectory = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $DestinationDirectory)) {
        New-Item -ItemType Directory -Path $DestinationDirectory | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    Write-Host "  Copied: $RelativePath" -ForegroundColor Gray
}

if ($MissingFiles.Count -gt 0) {
    Stop-Packaging "Required allowlisted files are missing: $($MissingFiles -join ', ')"
}

Write-Host "Running forbidden-entry guard gate..." -ForegroundColor Cyan
$ForbiddenEntries = @()
foreach ($Item in Get-ChildItem -LiteralPath $StagingDir -Recurse -Force) {
    $RelativePath = $Item.FullName.Substring($StagingDir.Length).TrimStart('\', '/')
    if ($Item.PSIsContainer) {
        $RelativePath += "/"
    }
    $Name = $Item.Name

    foreach ($Pattern in $ForbiddenPatterns) {
        $IsAnchored = $Pattern -match '\\$'
        if ($IsAnchored) {
            if ($Name -match $Pattern) {
                $ForbiddenEntries += "$RelativePath (matched $Pattern)"
                break
            }
        } else {
            if ($Name -eq $Pattern) {
                $ForbiddenEntries += "$RelativePath (matched $Pattern)"
                break
            }
        }
    }
}

if ($ForbiddenEntries.Count -gt 0) {
    Stop-Packaging "Forbidden entries found in staging:`n$($ForbiddenEntries -join "`n")"
}

$TarCommand = $null
foreach ($Candidate in @("$env:WINDIR\System32\tar.exe", "$env:WINDIR\Sysnative\tar.exe")) {
    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path -LiteralPath $Candidate)) {
        $TarCommand = $Candidate
        break
    }
}
if (-not $TarCommand) {
    $FoundTar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($FoundTar) {
        $TarCommand = $FoundTar.Source
    }
}
if (-not $TarCommand) {
    Stop-Packaging "Windows tar.exe was not found."
}

Write-Host "Creating portable ZIP with tar.exe..." -ForegroundColor Cyan
$TarOutput = & $TarCommand -a -cf $ZipPath -C $StagingParent $StagingFolder 2>&1
$TarExitCode = $LASTEXITCODE
if ($TarExitCode -ne 0) {
    if ($TarOutput) {
        Write-Host ($TarOutput -join "`n") -ForegroundColor Red
    }
    Stop-Packaging "tar.exe failed while creating the ZIP archive."
}
if (-not (Test-Path -LiteralPath $ZipPath) -or (Get-Item -LiteralPath $ZipPath).Length -eq 0) {
    Stop-Packaging "tar.exe did not create a non-empty ZIP archive."
}

Write-Host "Validating ZIP table of contents..." -ForegroundColor Cyan
$ArchiveOutput = & $TarCommand -tf $ZipPath 2>&1
$ArchiveExitCode = $LASTEXITCODE
if ($ArchiveExitCode -ne 0) {
    if ($ArchiveOutput) {
        Write-Host ($ArchiveOutput -join "`n") -ForegroundColor Red
    }
    Stop-Packaging "tar.exe failed while reading the ZIP table of contents."
}

$NormalizedEntries = @(
    foreach ($Entry in $ArchiveOutput) {
        $Entry.ToString().Replace('\', '/').TrimStart('./')
    }
)
if ($NormalizedEntries.Count -eq 0) {
    Stop-Packaging "The ZIP archive has no table-of-contents entries."
}

$TopLevelFolders = @()
foreach ($Entry in $NormalizedEntries) {
    $Parts = @($Entry.TrimEnd('/').Split('/') | Where-Object { $_ })
    if ($Parts.Count -gt 0 -and $TopLevelFolders -notcontains $Parts[0]) {
        $TopLevelFolders += $Parts[0]
    }
}
if ($TopLevelFolders.Count -ne 1 -or $TopLevelFolders[0] -ne $StagingFolder) {
    Stop-Packaging "The archive must contain exactly one top-level folder: $StagingFolder"
}

$ArchiveForbiddenEntries = @()
foreach ($Entry in $NormalizedEntries) {
    $Segments = $Entry -split '[\\/]'
    foreach ($Segment in $Segments) {
        if (-not $Segment) { continue }
        foreach ($Pattern in $ForbiddenPatterns) {
            $IsAnchored = $Pattern -match '\\$'
            if ($IsAnchored) {
                if ($Segment -match $Pattern) {
                    $ArchiveForbiddenEntries += "$Entry (matched $Pattern on segment '$Segment')"
                    break
                }
            } else {
                if ($Segment -eq $Pattern) {
                    $ArchiveForbiddenEntries += "$Entry (matched $Pattern on segment '$Segment')"
                    break
                }
            }
        }
    }
}
if ($ArchiveForbiddenEntries.Count -gt 0) {
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Stop-Packaging "Forbidden entries found in ZIP:`n$($ArchiveForbiddenEntries -join "`n")"
}

$MissingArchiveFiles = @()
foreach ($RelativePath in $AllowedFiles) {
    $RequiredEntry = "$StagingFolder/$RelativePath"
    if ($NormalizedEntries -notcontains $RequiredEntry) {
        $MissingArchiveFiles += $RequiredEntry
    }
}
if ($MissingArchiveFiles.Count -gt 0) {
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Stop-Packaging "Required files are missing from the ZIP:`n$($MissingArchiveFiles -join "`n")"
}

if ($Upload) {
    $GhCommand = Get-Command gh.exe -ErrorAction SilentlyContinue
    if (-not $GhCommand) {
        $GhCommand = Get-Command gh -ErrorAction SilentlyContinue
    }
    if (-not $GhCommand) {
        Stop-Packaging "The GitHub CLI (gh) is required for -Upload."
    }

    Write-Host "Uploading release asset to GitHub Release v$Version..." -ForegroundColor Cyan
    & $GhCommand.Source release upload "v$Version" $ZipPath --clobber
    $GhExitCode = $LASTEXITCODE
    if ($GhExitCode -ne 0) {
        Stop-Packaging "GitHub release upload failed with exit code $GhExitCode."
    }
}

$ArchiveSizeMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 2)
Write-Host "ZIP validation passed: bad_entries=0, all required files present." -ForegroundColor Green
Write-Host "Success! Created $ZipPath ($ArchiveSizeMB MB)" -ForegroundColor Green
