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

# Read required browser revisions from driver's browsers.json
$BrowsersJsonPath = python -c "import pathlib, playwright; print(pathlib.Path(playwright.__file__).parent / 'driver' / 'package' / 'browsers.json')"
if (-not (Test-Path -LiteralPath $BrowsersJsonPath)) {
    Write-Host "Error: browsers.json not found at $BrowsersJsonPath" -ForegroundColor Red
    exit 1
}
$BrowsersJson = Get-Content -Raw -LiteralPath $BrowsersJsonPath | ConvertFrom-Json
$FirefoxRev = ($BrowsersJson.browsers | Where-Object { $_.name -eq 'firefox' }).revision
$WinlddRev = ($BrowsersJson.browsers | Where-Object { $_.name -eq 'winldd' }).revision
if (-not $FirefoxRev -or -not $WinlddRev) {
    Write-Host "Error: Could not determine required Firefox or winldd revision from browsers.json" -ForegroundColor Red
    exit 1
}

$FirefoxFolderName = "firefox-$FirefoxRev"
$FirefoxFolder = Join-Path $PlaywrightRoot $FirefoxFolderName
if (-not (Test-Path -LiteralPath $FirefoxFolder)) {
    Write-Host "Error: Required Firefox revision folder not found: $FirefoxFolder" -ForegroundColor Red
    exit 1
}
$TargetBrowserDir = Join-Path $ExpectedExeDir "browsers"
if (-not (Test-Path -LiteralPath $TargetBrowserDir)) {
    New-Item -ItemType Directory -Path $TargetBrowserDir | Out-Null
}
$Destination = Join-Path $TargetBrowserDir $FirefoxFolderName
Copy-Item -LiteralPath $FirefoxFolder -Destination $Destination -Recurse -Force
Write-Host "Bundled Firefox browser from $FirefoxFolder into $Destination" -ForegroundColor Green

$FirefoxExe = Join-Path $Destination "firefox\firefox.exe"
if (-not (Test-Path -LiteralPath $FirefoxExe)) {
    $FirefoxExe = Join-Path $Destination "firefox.exe"
}
if (-not (Test-Path -LiteralPath $FirefoxExe)) {
    Write-Host "Error: bundled Firefox is missing firefox.exe at $FirefoxExe" -ForegroundColor Red
    exit 1
}
Write-Host "Bundled Firefox verified at $FirefoxExe" -ForegroundColor Green

$WinlddFolderName = "winldd-$WinlddRev"
$WinlddFolder = Join-Path $PlaywrightRoot $WinlddFolderName
if (-not (Test-Path -LiteralPath $WinlddFolder)) {
    Write-Host "Error: Required winldd revision folder not found: $WinlddFolder" -ForegroundColor Red
    exit 1
}
$WinlddDest = Join-Path $TargetBrowserDir $WinlddFolderName
Copy-Item -LiteralPath $WinlddFolder -Destination $WinlddDest -Recurse -Force
Write-Host "Bundled winldd helper from $WinlddFolder into $WinlddDest" -ForegroundColor Green
$PrintDeps = Join-Path $WinlddDest "PrintDeps.exe"
if (Test-Path -LiteralPath $PrintDeps) {
    Write-Host "Bundled winldd PrintDeps.exe verified at $PrintDeps" -ForegroundColor Green
}