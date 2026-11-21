param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [Parameter(Mandatory = $true)][string]$InstallerLog
)

$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
if (Test-Path -LiteralPath $InstallDirectory) {
    throw "Smoke installation must start with an absent destination: $InstallDirectory"
}
$setup = Start-Process -FilePath $installerPath `
    -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', "/DIR=`"$InstallDirectory`"", "/LOG=`"$InstallerLog`"") `
    -Wait -PassThru
if ($setup.ExitCode -ne 0) {
    throw "Installer exited $($setup.ExitCode); see $InstallerLog"
}

$desktop = Join-Path $InstallDirectory 'scribeforge_desktop.exe'
$backend = Join-Path $InstallDirectory 'backend\scribeforge.exe'
if (!(Test-Path -LiteralPath $desktop -PathType Leaf) -or !(Test-Path -LiteralPath $backend -PathType Leaf)) {
    throw 'Installer did not lay out the desktop runner and bundled backend.'
}

$process = Start-Process -FilePath $desktop -WorkingDirectory $InstallDirectory -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        $process.Refresh()
        if ($process.HasExited) {
            throw "Installed desktop exited before opening its window: $($process.ExitCode)"
        }
        if ($process.MainWindowHandle -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($process.MainWindowHandle -eq [IntPtr]::Zero) {
        throw 'Installed desktop did not open a window within 45 seconds.'
    }
    # Detect startup faults that occur immediately after the HWND is created.
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited -or $process.MainWindowHandle -eq [IntPtr]::Zero) {
        throw 'Installed desktop did not keep its window open after startup.'
    }
    Write-Host "Installed desktop opened a window: $($process.MainWindowTitle)"
} finally {
    if (!$process.HasExited) {
        if (!$process.CloseMainWindow() -or !$process.WaitForExit(5000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    $process.Dispose()
}
