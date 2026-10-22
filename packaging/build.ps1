<#
Builds the standalone Windows app: dist\LMD-Fixer-<version>-win64.zip, holding
a "LMD Fixer" folder with "LMD Fixer.exe". The people it's for need nothing
else installed.

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1

The virtual environment and PyInstaller's working files go under
%LOCALAPPDATA%\LMD-Fixer-build. That's outside OneDrive on purpose: an
unzipped build is a few thousand files, and syncing every rebuild is slow.
Only the finished zip is written into the repo (dist\ is git-ignored). The
environment is rebuilt from scratch whenever requirements-build.txt changes.

After building, the self-test checks the app starts (offscreen, in every
theme). If the example .ptp files are in lmd_fixer\tests (they're git-ignored,
so only on machines that have them), it also runs the full review through
the built .exe and through the source code, and fails unless the two outputs
are byte-for-byte identical. The .exe has no console, so it reports through a
file (--report) and its exit code.
#>
param(
    [string]$WorkDir = (Join-Path $env:LOCALAPPDATA "LMD-Fixer-build"),
    [string]$Python = "python",
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

# Native tools write progress to stderr, which Windows PowerShell can treat as
# an error, so judge them by exit code instead.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# Runs the windowed .exe's self-test: waits for it, shows its report, and
# fails on a non-zero exit code. Arguments are quoted, since paths have spaces.
function Invoke-ExeSelfTest([string]$What, [string]$Exe, [string[]]$Arguments, [string]$Report) {
    if (Test-Path $Report) { Remove-Item $Report }
    $quoted = ($Arguments + @("--report", $Report) | ForEach-Object { '"' + $_ + '"' }) -join " "
    $p = Start-Process -FilePath $Exe -ArgumentList "--selftest $quoted" -Wait -PassThru
    if (Test-Path $Report) { Get-Content $Report | ForEach-Object { Write-Host $_ } }
    if ($p.ExitCode -ne 0) { throw "$What failed (exit code $($p.ExitCode))" }
}

$Venv = Join-Path $WorkDir "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Requirements = Join-Path $Repo "packaging\requirements-build.txt"
$Stamp = Join-Path $Venv "requirements.sha256"
$Wanted = (Get-FileHash $Requirements -Algorithm SHA256).Hash
if ((Test-Path $Py) -and (-not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne $Wanted)) {
    Write-Host "Build requirements changed: recreating $Venv"
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $Py)) {
    Write-Host "Creating build environment in $Venv"
    Invoke-Native "Creating the virtual environment" { & $Python -m venv $Venv }
}
Write-Host "Installing pinned dependencies"
Invoke-Native "pip install" {
    & $Py -m pip install --quiet --disable-pip-version-check -r $Requirements
}
Set-Content -Path $Stamp -Value $Wanted

$Version = (& $Py -c "import lmd_fixer; print(lmd_fixer.__version__)").Trim()
Write-Host "Building LMD Fixer v$Version"

$Icon = Join-Path $WorkDir "icon.ico"
Invoke-Native "Drawing the icon" { & $Py -m lmd_fixer.desktop.icon $Icon }
$env:LMD_FIXER_ICON = $Icon

$Dist = Join-Path $WorkDir "dist"
Invoke-Native "PyInstaller" {
    & $Py -m PyInstaller --noconfirm --clean --log-level WARN `
        --distpath $Dist --workpath (Join-Path $WorkDir "work") `
        (Join-Path $Repo "packaging\lmd_fixer.spec")
}

$App = Join-Path $Dist "LMD Fixer"
$Exe = Join-Path $App "LMD Fixer.exe"
# Beside the .exe so users can find and edit it (see cli._use_editable_settings).
Copy-Item (Join-Path $Repo "lmd_fixer\fix_settings.toml") $App
Copy-Item (Join-Path $Repo "packaging\README.txt") $App

if (-not $SkipSelfTest) {
    $Out = Join-Path $WorkDir "selftest"
    New-Item -ItemType Directory -Force $Out | Out-Null
    Write-Host "`nSelf-test: built app"
    Invoke-ExeSelfTest "Self-test" $Exe @() (Join-Path $Out "app.txt")

    foreach ($name in @("O1140 - Original.ptp", "O1145.ptp")) {
        $source = Join-Path $Repo "lmd_fixer\tests\$name"
        if (-not (Test-Path $source)) {
            Write-Host "skip $name (not on this machine)"
            continue
        }
        $built = Join-Path $Out "$name.built"
        $fromSource = Join-Path $Out "$name.source"
        Write-Host "`nSelf-test: $name through the built app"
        Invoke-ExeSelfTest "Self-test on $name" $Exe @($source, $built) (Join-Path $Out "$name.txt")
        Write-Host "Self-test: $name through the source code"
        Invoke-Native "Source self-test on $name" { & $Py -m lmd_fixer.cli --selftest $source $fromSource }
        if ((Get-FileHash $built).Hash -ne (Get-FileHash $fromSource).Hash) {
            throw "Built app and source code produced different output for $name"
        }
        Write-Host "ok   identical output from the built app and the source code"
    }
}

$Zip = Join-Path $Repo "dist\LMD-Fixer-$Version-win64.zip"
New-Item -ItemType Directory -Force (Split-Path $Zip) | Out-Null
if (Test-Path $Zip) { Remove-Item $Zip }
Write-Host "`nZipping"
Compress-Archive -Path $App -DestinationPath $Zip
$sizeMb = [math]::Round((Get-Item $Zip).Length / 1MB)
Write-Host "Done: $Zip ($sizeMb MB)"
