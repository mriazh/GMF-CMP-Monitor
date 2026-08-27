param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

$SpecFile = Join-Path $RepoRoot "gmf_cmp_monitor.spec"
$DistDir = Join-Path $RepoRoot "dist"
$ExpectedExeDir = Join-Path $DistDir "GMF-CMP-Monitor"
$ExpectedExe = Join-Path $ExpectedExeDir "GMF-CMP-Monitor.exe"

function Get-PyInstaller {
    $Candidate = Join-Path $RepoRoot ".venv312\Scripts\pyinstaller.exe"
    if (Test-Path -LiteralPath $Candidate) {
        return $Candidate
    }
    $Found = Get-Command pyinstaller -ErrorAction SilentlyContinue
    if ($Found) {
        return $Found.Source
    }
    return $null
}

$PyInstallerPath = Get-PyInstaller
if (-not $PyInstallerPath) {
    Write-Host "Error: pyinstaller is not available. Install it in the project venv or on PATH." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $SpecFile)) {
    Write-Host "Error: gmf_cmp_monitor.spec is missing at $SpecFile." -ForegroundColor Red
    exit 1
}

if (Test-Path (Join-Path $RepoRoot "build")) { Remove-Item -Recurse -Force (Join-Path $RepoRoot "build") }
if (Test-Path (Join-Path $RepoRoot "dist")) { Remove-Item -Recurse -Force (Join-Path $RepoRoot "dist") }

Write-Host "Building GMF-CMP-Monitor.exe using $SpecFile..." -ForegroundColor Cyan
& $PyInstallerPath -y $SpecFile

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $ExpectedExe)) {
    Write-Host "Error: Expected executable not found at $ExpectedExe." -ForegroundColor Red
    exit 1
}
Write-Host "Build verified. Executable found at $ExpectedExe" -ForegroundColor Green

$PlaywrightRoot = Join-Path $env:LOCALAPPDATA "ms-playwright"
$FirefoxFolders = Get-ChildItem -LiteralPath $PlaywrightRoot -Force -Directory -Filter "firefox-*" -ErrorAction SilentlyContinue
if (-not $FirefoxFolders -or $FirefoxFolders.Count -eq 0) {
    Write-Host "Error: No Playwright Firefox browser found under $PlaywrightRoot. Run 'playwright install firefox' first." -ForegroundColor Red
    exit 1
}
$FirefoxFolder = $FirefoxFolders[0]
$TargetBrowserDir = Join-Path $ExpectedExeDir "browsers"
if (-not (Test-Path -LiteralPath $TargetBrowserDir)) {
    New-Item -ItemType Directory -Path $TargetBrowserDir | Out-Null
}
$Destination = Join-Path $TargetBrowserDir $FirefoxFolder.Name
Copy-Item -LiteralPath $FirefoxFolder.FullName -Destination $Destination -Recurse -Force
Write-Host "Bundled Firefox browser from $($FirefoxFolder.FullName) into $Destination" -ForegroundColor Green

$FirefoxExe = Join-Path $Destination "firefox\firefox.exe"
if (-not (Test-Path -LiteralPath $FirefoxExe)) {
    $FirefoxExe = Join-Path $Destination "firefox.exe"
}
if (-not (Test-Path -LiteralPath $FirefoxExe)) {
    Write-Host "Error: bundled Firefox is missing firefox.exe at $FirefoxExe" -ForegroundColor Red
    exit 1
}
Write-Host "Bundled Firefox verified at $FirefoxExe" -ForegroundColor Green

$WinlddFolders = Get-ChildItem -LiteralPath $PlaywrightRoot -Force -Directory -Filter "winldd-*" -ErrorAction SilentlyContinue
if ($WinlddFolders -and $WinlddFolders.Count -gt 0) {
    $WinlddFolder = $WinlddFolders[0]
    $WinlddDest = Join-Path $TargetBrowserDir $WinlddFolder.Name
    Copy-Item -LiteralPath $WinlddFolder.FullName -Destination $WinlddDest -Recurse -Force
    Write-Host "Bundled winldd helper from $($WinlddFolder.FullName) into $WinlddDest" -ForegroundColor Green
    $PrintDeps = Join-Path $WinlddDest "PrintDeps.exe"
    if (Test-Path -LiteralPath $PrintDeps) {
        Write-Host "Bundled winldd PrintDeps.exe verified at $PrintDeps" -ForegroundColor Green
    }
}