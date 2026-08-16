param(
    [string]$Version = "v2.41.1-beta2.25.9"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$Dist = Join-Path $Root "dist"
$SafeVersion = ($Version -replace '[^A-Za-z0-9._-]', '-')
$Name = "FIFA-14-Local-FUT-$SafeVersion"
$Stage = Join-Path ([System.IO.Path]::GetTempPath()) ($Name + "-" + [guid]::NewGuid().ToString("N"))
$StageRoot = Join-Path $Stage $Name
$ZipPath = Join-Path $Dist ($Name + ".zip")

$RepoOnly = @(
    ".git",
    ".github",
    ".venv",
    "venv",
    "artifacts",
    "reports",
    "logs",
    "captures",
    "runtime",
    "certs",
    "state",
    "dist"
)

$RepoDocs = @(
    ".gitignore",
    ".gitattributes",
    "SETUP_GITHUB_REPO.cmd",
    "PUSH_TO_GITHUB.cmd",
    "PACKAGE_RELEASE.cmd"
)

# Local to whoever built the package, and published to everyone who downloads it:
# config.local.psd1 holds the builder's FIFA install path (so their Windows user
# name), local-fut-settings.json is their own tuning, and a Windows .lnk embeds
# absolute paths. These are excluded by .gitignore but the packager copies from
# the working folder, not from Git, so they have to be named here too.
$LocalOnly = @(
    "config.local.psd1",
    "local-fut-settings.json"
)
$LocalOnlyPatterns = @(
    "*.lnk"
)

try {
    New-Item -ItemType Directory -Force -Path $Dist | Out-Null
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null

    Get-ChildItem -LiteralPath $Root -Force | ForEach-Object {
        if ($RepoOnly -contains $_.Name) { return }
        if ($RepoDocs -contains $_.Name) { return }
        if ($LocalOnly -contains $_.Name) {
            Write-Host "[skip] $($_.Name) is local to this machine"
            return
        }
        foreach ($pattern in $LocalOnlyPatterns) {
            if ($_.Name -like $pattern) {
                Write-Host "[skip] $($_.Name) is local to this machine"
                return
            }
        }

        $Destination = Join-Path $StageRoot $_.Name
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }

    $Packager = Join-Path $StageRoot "tools\package_release.ps1"
    if (Test-Path -LiteralPath $Packager) {
        Remove-Item -LiteralPath $Packager -Force
    }

    # The filter above only sees top-level entries, and directories are copied
    # whole -- so compiled caches nested inside server\ and tools\ came along.
    # A .pyc embeds the absolute path it was compiled from, which means the
    # builder's Windows user name shipped to everyone who downloaded the ZIP.
    # Use -Filter, not -Include: with a -LiteralPath that has no wildcard,
    # -Include is silently ignored and the pipeline yields *every* file.
    Get-ChildItem -LiteralPath $StageRoot -Recurse -Force -Directory -Filter "__pycache__" |
        Sort-Object -Property FullName -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    foreach ($compiled in @("*.pyc", "*.pyo")) {
        Get-ChildItem -LiteralPath $StageRoot -Recurse -Force -File -Filter $compiled |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }

    # Verify the package, not the working folder. A release is a *different tree*
    # from the repo -- no .gitignore, no .git, no artifacts -- and 0.2-beta
    # shipped unable to start because a verifier read a file the packager strips
    # (dzevallos/f14-localfut#1). The launcher runs these before startup, so if
    # they fail here they would fail for everyone who downloads this.
    $StagedVerifiers = @(
        "verify_fifa14_v237_install.py",
        "verify_fifa14_beta2.py"
    )
    foreach ($verifier in $StagedVerifiers) {
        $verifierPath = Join-Path $StageRoot "tools\$verifier"
        if (-not (Test-Path -LiteralPath $verifierPath)) { continue }
        Write-Host "[check] $verifier against the staged package..."
        # Windows PowerShell 5.1 turns *any* native stderr line into a
        # NativeCommandError, which $ErrorActionPreference = "Stop" then treats
        # as fatal even when the process exits 0 -- and these verifiers write
        # ordinary diagnostics to stderr. Relax the preference across the call
        # and trust the exit code, which is the only reliable signal here.
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonExe $verifierPath *> $null
            $verifierExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($verifierExit -ne 0) {
            throw ("Refusing to package: $verifier fails inside the release tree, " +
                   "so the launcher would refuse to start for anyone who downloads it. " +
                   "Re-run it directly to see why: python tools\$verifier")
        }
    }

    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }

    Compress-Archive -LiteralPath $StageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
    Write-Host "[OK] Release ZIP: $ZipPath"
}
finally {
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
