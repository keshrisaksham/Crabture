<#
.SYNOPSIS
    Build Crabture for Windows: a PyInstaller bundle and (optionally) a Setup.exe installer.

.DESCRIPTION
    Run from the project root in PowerShell, with Python 3.9+ installed:

        powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1            # bundle only
        powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Installer # + Setup.exe

    The installer needs Inno Setup 6 (https://jrsoftware.org/isinfo.php, or `winget install JRSoftware.InnoSetup`).

    Output:
        dist\Crabture\Crabture.exe                 the app (run it, or zip the folder)
        dist\Crabture-<version>-Setup.exe          the installer (with -Installer)

    Everything happens in a throw-away virtual environment (.venv-build); your global Python is not touched.
#>
param(
    [switch]$Installer,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (Select-String -Path "crabture\version.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "==> Building Crabture $Version for Windows"

if ($Clean) { Remove-Item -Recurse -Force build, dist, .venv-build -ErrorAction SilentlyContinue }

# 1. build environment
if (-not (Test-Path ".venv-build\Scripts\python.exe")) {
    Write-Host "==> Creating .venv-build"
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { & py -3 -m venv .venv-build } else { & python -m venv .venv-build }
}
$Python = Join-Path $Root ".venv-build\Scripts\python.exe"
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet "PyQt6>=6.4" pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. icon (assets\logo.ico is generated from assets\logo.png)
& $Python scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "could not create the icon" }

# 3. PyInstaller (one-folder: starts faster and trips antivirus heuristics far less than --onefile)
Remove-Item -Recurse -Force build, dist\Crabture -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean --windowed `
    --name Crabture `
    --icon "assets\logo.ico" `
    --specpath build `
    --add-data "$Root\assets\logo.png;assets" `
    --add-data "$Root\assets\logo.ico;assets" `
    --collect-submodules crabture `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "==> Bundle ready: dist\Crabture\Crabture.exe"

# 4. installer
if ($Installer) {
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $candidates = @("$env:ProgramFiles\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
        $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $found) { throw "Inno Setup 6 was not found. Install it:  winget install JRSoftware.InnoSetup" }
        $iscc = $found
    } else { $iscc = $iscc.Source }
    & $iscc "/DAppVersion=$Version" "/DRootDir=$Root" "packaging\windows\crabture.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "==> Installer ready: dist\Crabture-$Version-Setup.exe"
}
