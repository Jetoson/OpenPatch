# Installs pending Windows Updates through the Windows Update Agent COM API.
#   exit 0  every selected update installed, or there was nothing to install
#   exit 1  at least one update failed, or updates could not be searched for

param(
    [string]$KB,
    # How recently a restore point may have been created and still let this
    # run skip making its own. 0 (the default) always creates one; -1 leaves
    # the machine's own setting alone. See _restore_point.ps1.
    [int]$CheckpointThrottleMinutes = 0
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}


if (-not (Test-Elevated)) {
    Write-Output "ABORTED: installing Windows Updates requires elevation."
    exit 1
}

function Format-UpdateKbs {
    param($Update)
    $ids = @()
    foreach ($id in $Update.KBArticleIDs) { $ids += "KB$id" }
    if ($ids.Count) { return ($ids -join ", ") }
    return "(no KB)"
}

function Test-KbMatch {
    param($Update, [string]$Wanted)
    $target = ($Wanted -replace '(?i)^kb', '').Trim()
    foreach ($id in $Update.KBArticleIDs) {
        if ("$id".Trim() -eq $target) { return $true }
    }
    return $false
}

# Search
try {
    $session = New-Object -ComObject Microsoft.Update.Session
    # Identifies OpenPatch in the Windows Update client log
    $session.ClientApplicationID = "OpenPatch"
    $searcher = $session.CreateUpdateSearcher()

    Write-Output "Searching for applicable updates..."
    $searchResult = $searcher.Search("IsInstalled=0 and IsHidden=0")
} catch {
    Write-Output "FAILED: update search failed: $($_.Exception.Message)"
    exit 1
}

$candidates = @()
foreach ($update in $searchResult.Updates) {
    if ($KB -and -not (Test-KbMatch -Update $update -Wanted $KB)) { continue }
    $candidates += $update
}

if ($candidates.Count -eq 0) {
    if ($KB) {
        Write-Output "FAILED: no applicable update matching '$KB' is available on this machine."
        exit 1
    }
    Write-Output "No pending Windows Updates - the machine is already current."
    exit 0
}

Write-Output "$($candidates.Count) update(s) to apply:"
foreach ($update in $candidates) {
    Write-Output "  - $($update.Title) [$(Format-UpdateKbs -Update $update)]"
}

# Restore checkpoint
# After deciding there is something to install and before installing it.
# Best-effort, i.e. a machine that cannot take a checkpoint shouldn't refuse patching
$CheckpointDescription = "OpenPatch Pre-Update"
. "$PSScriptRoot\_restore_point.ps1"
$checkpoint = New-OpenPatchCheckpoint -Description $CheckpointDescription `
                                      -ThrottleMinutes $CheckpointThrottleMinutes
Write-Output $checkpoint.Message

# Accept EULAs
# For those updates who requires EULA acceptance.
foreach ($update in $candidates) {
    if (-not $update.EulaAccepted) {
        try { $update.AcceptEula() } catch {
            Write-Output "  ! could not accept EULA for $($update.Title): $($_.Exception.Message)"
        }
    }
}

# Download the updates
$toDownload = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($update in $candidates) {
    if (-not $update.IsDownloaded) { $null = $toDownload.Add($update) }
}

if ($toDownload.Count -gt 0) {
    Write-Output "Downloading $($toDownload.Count) update(s)..."
    try {
        $downloader = $session.CreateUpdateDownloader()
        $downloader.Updates = $toDownload
        $downloadResult = $downloader.Download()
        # 2 = Succeeded, 3 = SucceededWithErrors. Anything else means nothing
        # usable landed on disk, so there is no point continuing to install.
        if ($downloadResult.ResultCode -notin @(2, 3)) {
            Write-Output "FAILED: download finished with result code $($downloadResult.ResultCode) (HRESULT 0x$('{0:X8}' -f $downloadResult.HResult))."
            exit 1
        }
    } catch {
        Write-Output "FAILED: download failed: $($_.Exception.Message)"
        exit 1
    }
} else {
    Write-Output "All selected updates are already downloaded."
}

# Install
$toInstall = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($update in $candidates) {
    if ($update.IsDownloaded) { $null = $toInstall.Add($update) }
}

if ($toInstall.Count -eq 0) {
    Write-Output "FAILED: no update was downloaded successfully"
    exit 1
}

$installer = $session.CreateUpdateInstaller()
if ($installer.IsBusy) {
    # Windows serialises installs
    Write-Output "FAILED: another Windows Update installation is already in progress on this machine."
    exit 1
}

Write-Output "Installing $($toInstall.Count) update(s)..."
try {
    $installer.Updates = $toInstall
    $installResult = $installer.Install()
} catch {
    Write-Output "FAILED: installation failed: $($_.Exception.Message)"
    exit 1
}

# Report
# Per-update rather than only the aggregate
$succeeded = 0
$failed = 0
for ($i = 0; $i -lt $toInstall.Count; $i++) {
    $update = $toInstall.Item($i)
    $result = $installResult.GetUpdateResult($i)
    switch ($result.ResultCode) {
        2 { $succeeded++; Write-Output "  [ok]      $($update.Title)" }
        3 { $succeeded++; Write-Output "  [partial] $($update.Title) - installed with errors (HRESULT 0x$('{0:X8}' -f $result.HResult))" }
        default {
            $failed++
            Write-Output "  [failed]  $($update.Title) - result code $($result.ResultCode), HRESULT 0x$('{0:X8}' -f $result.HResult)"
        }
    }
}

Write-Output "Installed $succeeded of $($toInstall.Count) update(s); $failed failed."

# Reported, only the operator decides manually whether to reboot or not.
if ($installResult.RebootRequired) {
    Write-Output "REBOOT REQUIRED: the machine must restart to finish applying these updates."
}

if ($failed -gt 0) { exit 1 }
exit 0
