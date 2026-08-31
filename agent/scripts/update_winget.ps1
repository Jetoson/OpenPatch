# Applies pending winget upgrades, after taking a System Restore checkpoint
#   exit 0  updates applied (checkpoint may or may not have been created)
#   exit 1  the update itself failed

param(
    # Upgrade only this winget package id.
    [string]$PackageId,
    [int]$CheckpointThrottleMinutes = 0
)

$ErrorActionPreference = "Continue"
$CheckpointDescription = "OpenPatch Pre-Update"

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Restore checkpoint
if (-not (Test-Elevated)) {
    Write-Output "WARNING: not elevated - cannot create a restore point. Automatic rollback will NOT be available for this update."
} else {
    . "$PSScriptRoot\_restore_point.ps1"
    $checkpoint = New-OpenPatchCheckpoint -Description $CheckpointDescription `
                                          -ThrottleMinutes $CheckpointThrottleMinutes
    Write-Output $checkpoint.Message
}

# Apply the updates
# Resolved rather than invoked as "winget"1.
. "$PSScriptRoot\_winget.ps1"
$winget = Resolve-Winget
if (-not $winget) {
    Write-Error "Winget could not be located (App Installer missing, or not reachable from this account)."
    exit 1
}

$common = @(
    "--silent", "--disable-interactivity",
    "--accept-source-agreements", "--accept-package-agreements"
)

if ($PackageId) {
    # winget truncates long ids with an ellipsis in its table output, and that
    # table is where the dashboard's package ids come from.
    if ($PackageId -match '[…]' -or $PackageId -like "*...") {
        Write-Error "Package id '$PackageId' is truncated, so the exact package cannot be identified. Run a full upgrade instead."
        exit 1
    }

    Write-Output "Upgrading single package '$PackageId' using $winget"
    # --exact since it should match the exact package ID
    $output = & $winget upgrade --id $PackageId --exact @common 2>&1 | Out-String
} else {
    Write-Output "Applying all winget upgrades using $winget"
    $output = & $winget upgrade --all @common 2>&1 | Out-String
}
$wingetExit = $LASTEXITCODE

Write-Output $output.Trim()

# winget returns assorted non-zero codes. -1978335189 (0x8A15002B) is APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE,
# i.e. everything is already current, which is a success for our purposes.
if ($wingetExit -eq 0 -or $wingetExit -eq -1978335189) {
    Write-Output "winget completed (exit $wingetExit)."
    exit 0
}

Write-Error "winget failed with exit code $wingetExit."
exit 1
