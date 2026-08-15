param(
    [int]$Coins = 1000000
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
# Tops the FUT club up to $Coins. Running it while the balance is already at or
# above the target does nothing, so it restores a spent test float rather than
# stacking grants. This replaces give_100m_test_coins.ps1, which asked for a
# one-shot grant keyed on a fixed build id: once that ledger row existed it
# silently granted nothing on every later run.
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Local Python environment is missing; creating it now."
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}
if ($Coins -le 0) { throw "-Coins must be a positive target balance." }
$persistentDir = Join-Path $env:LOCALAPPDATA "FIFA14LocalFUTBeta"
$db = Join-Path $persistentDir "local-fut-beta-v2410.sqlite3"
if (-not (Test-Path -LiteralPath $db)) { throw "No BETA save found at $db - launch the game once first." }
$fifa = Get-Process -Name fifa14 -ErrorAction SilentlyContinue
if ($fifa) { throw "fifa14.exe is running. Close the game before editing the save." }
$result = & $venvPython (Join-Path $PSScriptRoot "prepare_fifa14_beta_state.py") --database $db --test-coins $Coins
if ($LASTEXITCODE -ne 0) { throw "Could not apply the test-coin top-up." }
$grant = ($result | ConvertFrom-Json).consumablesTestGrant
if ($grant.granted -gt 0) {
    Write-Host ("Topped up {0:N0} -> {1:N0} coins (+{2:N0})." -f $grant.balanceBefore, $grant.balanceAfter, $grant.granted) -ForegroundColor Green
} else {
    Write-Host ("Balance is already {0:N0}, at or above the {1:N0} target; nothing granted." -f $grant.balanceBefore, $Coins) -ForegroundColor Yellow
}
