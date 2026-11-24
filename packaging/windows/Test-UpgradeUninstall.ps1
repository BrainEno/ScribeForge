<#
.SYNOPSIS
  Upgrade / failed-install / uninstall / reinstall data-preservation smoke (issue #245).

.DESCRIPTION
  Assumes Test-InstalledDesktop.ps1 already performed the fresh install + launch
  into -InstallDirectory (that script requires an absent directory; this one
  requires an existing installation). Seeds three classes of user data outside
  the install directory, then verifies each phase preserves them byte-for-byte:

    - configuration : %APPDATA%\ScribeForge\settings.json
    - project data  : a user-chosen project folder (simulated under RUNNER_TEMP)
    - model/runtime : %LOCALAPPDATA%\ScribeForge\runtime marker

  Phases: upgrade over the existing install, a deliberately failing install
  (uncreatable /DIR), uninstall (app files must vanish, user data must not),
  and reinstall. Inno Setup has no [UninstallDelete] entries for these
  locations, so the uninstaller must never touch them.

.NOTES
  Combined with Test-InstalledDesktop.ps1 this covers install, launch, upgrade,
  uninstall and reinstall on the CI Windows runner. Signing/certificates and a
  manual clean Windows 11 VM walkthrough remain tracked in docs/.
#>
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [Parameter(Mandatory = $true)][string]$LogPrefix
)

$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$desktop = Join-Path $InstallDirectory 'scribeforge_desktop.exe'
$backend = Join-Path $InstallDirectory 'backend\scribeforge.exe'
if (!(Test-Path -LiteralPath $desktop -PathType Leaf)) {
    throw "Expected an existing installation at $InstallDirectory; run Test-InstalledDesktop.ps1 first."
}
if (!(Test-Path -LiteralPath $backend -PathType Leaf)) {
    throw "Installed backend missing at $backend; run Test-InstalledDesktop.ps1 first."
}

function Invoke-SilentSetup {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [string]$TargetDir = $InstallDirectory
    )
    $setup = Start-Process -FilePath $installerPath `
        -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', "/DIR=`"$TargetDir`"", "/LOG=`"$LogPath`"") `
        -Wait -PassThru
    return $setup.ExitCode
}

# --- Seed user data outside {app} -----------------------------------------
$settingsDir = Join-Path $env:APPDATA 'ScribeForge'
New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
$settingsFile = Join-Path $settingsDir 'settings.json'
$projectDir = Join-Path $env:RUNNER_TEMP 'ScribeForge Upgrade Project'
New-Item -ItemType Directory -Force -Path $projectDir | Out-Null
$projectMarker = Join-Path $projectDir 'project-marker.txt'
$runtimeDir = Join-Path $env:LOCALAPPDATA 'ScribeForge\runtime'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$runtimeMarker = Join-Path $runtimeDir 'models-marker.txt'

Set-Content -LiteralPath $settingsFile `
    -Value '{"schema_version":1,"upgrade_smoke":"preserve-me"}' -Encoding utf8
Set-Content -LiteralPath $projectMarker `
    -Value 'user project evidence must survive upgrade and uninstall' -Encoding utf8
Set-Content -LiteralPath $runtimeMarker `
    -Value 'downloaded model marker must survive upgrade and uninstall' -Encoding utf8

$tracked = @($settingsFile, $projectMarker, $runtimeMarker)

function Get-TrackedHashes {
    $map = @{}
    foreach ($file in $tracked) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Tracked user data file missing before the phase: $file"
        }
        $map[$file] = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
    }
    return $map
}

function Assert-TrackedUnchanged {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Expected,
        [Parameter(Mandatory = $true)][string]$Phase
    )
    foreach ($file in $tracked) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "[$Phase] user data file was deleted: $file"
        }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $Expected[$file]) {
            throw "[$Phase] user data file content changed: $file"
        }
    }
    Write-Host "[$Phase] settings, project and runtime user data preserved."
}

$baseline = Get-TrackedHashes

# --- 1. Upgrade over the existing installation ----------------------------
$code = Invoke-SilentSetup -LogPath "$LogPrefix-upgrade.log"
if ($code -ne 0) {
    throw "Upgrade installer exited $code; see $LogPrefix-upgrade.log"
}
if (!(Test-Path -LiteralPath $desktop -PathType Leaf) -or !(Test-Path -LiteralPath $backend -PathType Leaf)) {
    throw 'Upgrade removed the installed desktop or bundled backend.'
}
Assert-TrackedUnchanged -Expected $baseline -Phase 'upgrade'

# --- 2. A failing install must not lose data or the existing install ------
$blocked = Join-Path $env:RUNNER_TEMP 'scribeforge-blocked-parent'
Set-Content -LiteralPath $blocked -Value 'this file blocks directory creation' -Encoding utf8
$badDir = Join-Path $blocked 'ScribeForge'
$code = Invoke-SilentSetup -LogPath "$LogPrefix-failed.log" -TargetDir $badDir
if ($code -eq 0) {
    throw 'Installer unexpectedly succeeded with an uncreatable /DIR target.'
}
Write-Host "[failed-install] installer exited $code as expected."
if (!(Test-Path -LiteralPath $desktop -PathType Leaf)) {
    throw 'Failed install attempt removed the existing desktop.'
}
Assert-TrackedUnchanged -Expected $baseline -Phase 'failed-install'

# --- 3. Uninstall: app files go, user data stays --------------------------
$unins = Join-Path $InstallDirectory 'unins000.exe'
if (!(Test-Path -LiteralPath $unins -PathType Leaf)) {
    throw "Missing uninstaller: $unins"
}
$un = Start-Process -FilePath $unins `
    -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') `
    -Wait -PassThru
if ($un.ExitCode -ne 0) {
    throw "Uninstaller exited $($un.ExitCode)"
}
if (Test-Path -LiteralPath $desktop) {
    throw 'Uninstall left scribeforge_desktop.exe behind.'
}
if (Test-Path -LiteralPath $backend) {
    throw 'Uninstall left the bundled backend behind.'
}
Assert-TrackedUnchanged -Expected $baseline -Phase 'uninstall'

# --- 4. Reinstall into the same directory ---------------------------------
$code = Invoke-SilentSetup -LogPath "$LogPrefix-reinstall.log"
if ($code -ne 0) {
    throw "Reinstall exited $code; see $LogPrefix-reinstall.log"
}
if (!(Test-Path -LiteralPath $desktop -PathType Leaf) -or !(Test-Path -LiteralPath $backend -PathType Leaf)) {
    throw 'Reinstall did not restore the desktop and bundled backend.'
}
Assert-TrackedUnchanged -Expected $baseline -Phase 'reinstall'

Write-Host 'Upgrade/failed-install/uninstall/reinstall data-preservation smoke passed.'
