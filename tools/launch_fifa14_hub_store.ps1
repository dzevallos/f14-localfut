param([switch]$Elevated)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir

# Ensure the small production runtime compatibility layer under server\ is
# available to every Python process launched from this entry point, including
# the elevated relaunch below. Verifier scripts opt themselves out of the
# runtime-only adapter inside sitecustomize.py.
$runtimeServerPath = Join-Path $projectDir "server"
$pythonPathParts = @()
if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $pythonPathParts = @($env:PYTHONPATH -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}
if ($pythonPathParts -notcontains $runtimeServerPath) {
    $env:PYTHONPATH = (($runtimeServerPath) + @($pythonPathParts)) -join ';'
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host "Administrator rights are required for the local hosts/certificate setup and FIFA archive patching. Requesting elevation..."
    # -Wait and the child's exit code matter: without them this returned 0 the
    # instant the elevated window opened, so a failure over there closed its own
    # window and the caller's `pause` reported success. That is why a failing
    # verifier presented as "the window flashed and nothing happened".
    try {
        $elevatedProcess = Start-Process -FilePath "powershell.exe" -Verb RunAs -PassThru -Wait -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"'), "-Elevated"
        )
    } catch {
        Write-Host ""
        Write-Host "Elevation was refused or failed: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    if ($null -ne $elevatedProcess -and $elevatedProcess.ExitCode -ne 0) { exit $elevatedProcess.ExitCode }
    exit 0
}

# ---------------------------------------------------------------------------
# From here on this process does the work, so everything it prints is worth
# keeping. Transcribe to artifacts\launcher.log and never let the window close
# on an unread error.
# ---------------------------------------------------------------------------
$launcherLogDir = Join-Path $projectDir "artifacts"
$launcherLog = Join-Path $launcherLogDir "launcher.log"
$launcherTranscribing = $false
try {
    New-Item -ItemType Directory -Force -Path $launcherLogDir | Out-Null
    Start-Transcript -Path $launcherLog -Force | Out-Null
    $launcherTranscribing = $true
} catch {
    Write-Host "(could not start the launcher transcript: $($_.Exception.Message))" -ForegroundColor Yellow
}

function Stop-LauncherTranscript {
    if ($script:launcherTranscribing) {
        try { Stop-Transcript | Out-Null } catch { }
        $script:launcherTranscribing = $false
    }
}

trap {
    Stop-LauncherTranscript
    Write-Host ""
    Write-Host "==================== LAUNCH FAILED ====================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($null -ne $_.InvocationInfo) {
        Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "Full log, including any verifier output above: $launcherLog" -ForegroundColor Yellow
    Write-Host "Send that file if you want it looked at." -ForegroundColor Yellow
    Write-Host "=======================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Press Enter to close this window..."
    try { [void](Read-Host) } catch { Start-Sleep -Seconds 60 }
    exit 1
}

Write-Host "============================================================"
Write-Host " FIFA 14 LOCAL FUT v2.41.1 BETA 2.25.9 ISSUE HOTFIX"
Write-Host "============================================================"
Write-Host " BETA 2.25.9 FRIEND: clean local profile, starter bronze club, no bundled user save"
Write-Host ""

. (Join-Path $PSScriptRoot "common.ps1")
$resolvedGame = Resolve-Fifa14Paths -PromptIfMissing -PersistDetected
$GameRoot = $resolvedGame.GameRoot
$GameExe = $resolvedGame.GameExe
Write-Host ("FIFA 14 Game directory: " + $GameRoot + " [" + $resolvedGame.Source + "]") -ForegroundColor Green

# Friend-PC prerequisite preflight. This is cheap when everything is already
# installed; when Python or Git/OpenSSL is missing it installs only what is
# needed, then the normal local-venv bootstrap below handles pip packages.
$prerequisiteInstaller = Join-Path $PSScriptRoot "install_prerequisites.ps1"
if (-not (Test-Path -LiteralPath $prerequisiteInstaller -PathType Leaf)) {
    throw "Prerequisite installer is missing: $prerequisiteInstaller"
}
Write-Host "Checking friend-PC prerequisites (Python + Git/OpenSSL)..." -ForegroundColor Cyan
& $prerequisiteInstaller -SkipVenvBootstrap
if ($LASTEXITCODE -ne 0) {
    throw "Prerequisite setup failed. Run INSTALL_PREREQUISITES.cmd directly for the full diagnostic output."
}
Write-Host "Friend-PC prerequisites ready." -ForegroundColor Green

# Any existing FIFA process would keep the retail APT/DB archives locked. This
# launcher owns the FIFA session, so close only fifa14.exe before patching. EA
# App and every other process are left untouched.
$staleFifa = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
if ($staleFifa.Count -gt 0) {
    Write-Host ("Closing stale FIFA 14 process(es) before archive/database patching: " + (($staleFifa | ForEach-Object { $_.Id }) -join ", ")) -ForegroundColor Yellow
    $staleFifa | Stop-Process -Force -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $stillRunning = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
    } while ($stillRunning.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($stillRunning.Count -gt 0) {
        throw "Could not close stale fifa14.exe before on-disk patching."
    }
    Write-Host "Stale FIFA 14 process closed. Continuing with a clean patch/launch cycle." -ForegroundColor Green
}


$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Local Python environment is missing; creating it now."
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
} else {
    & $venvPython -c "import frida, cryptography" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Local Python dependencies are incomplete; repairing them now."
        & (Join-Path $PSScriptRoot "bootstrap.ps1")
    }
}

& (Join-Path $PSScriptRoot "run_fifa14_local_beta.ps1") -GameRoot $GameRoot -GameExe $GameExe
$sessionExitCode = $LASTEXITCODE
Stop-LauncherTranscript
if ($sessionExitCode -ne 0) {
    Write-Host ""
    Write-Host "The session exited with code $sessionExitCode. Full log: $launcherLog" -ForegroundColor Yellow
    Write-Host "Press Enter to close this window..."
    try { [void](Read-Host) } catch { Start-Sleep -Seconds 60 }
    exit $sessionExitCode
}
