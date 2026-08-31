# Self-healing rollback: restores the machine to the checkpoint that
# update_winget.ps1 took immediately before patching.
#   exit 0  a restore was initiated
#   exit 1  no usable checkpoint
# This deliberately fails closed. It only ever targets a restore point this
# agent created, identified by description AND recency.

param(
    # The default suits the automatic self-heal, which runs seconds after the
    # patch it is undoing.
    [int]$MaxAgeHours = 6
)

$ErrorActionPreference = "Continue"
$CheckpointDescription = "OpenPatch Pre-Update"

if ($MaxAgeHours -lt 1) {
    Write-Output "ROLLBACK ABORTED: -MaxAgeHours must be at least 1 (got $MaxAgeHours)."
    exit 1
}
$MaxCheckpointAgeHours = $MaxAgeHours

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Elevated)) {
    Write-Output "ROLLBACK ABORTED: the agent is not elevated, so it cannot read or apply restore points."
    exit 1
}

try {
    $points = @(Get-ComputerRestorePoint -ErrorAction Stop)
} catch {
    Write-Output "ROLLBACK ABORTED: could not read restore points: $($_.Exception.Message)"
    exit 1
}

$candidate = $points |
    Where-Object { $_.Description -eq $CheckpointDescription } |
    Sort-Object SequenceNumber -Descending |
    Select-Object -First 1

if (-not $candidate) {
    Write-Output "ROLLBACK ABORTED: no restore point named '$CheckpointDescription' exists. The pre-update checkpoint was never created (most likely the agent was unelevated, or Windows throttled it)."
    exit 1
}

# CreationTime comes back as a WMI datetime string, not a DateTime.
try {
    $created = [Management.ManagementDateTimeConverter]::ToDateTime($candidate.CreationTime)
} catch {
    $created = $null
}

if ($created) {
    $ageHours = ((Get-Date) - $created).TotalHours
    if ($ageHours -gt $MaxCheckpointAgeHours) {
        Write-Output ("ROLLBACK ABORTED: the newest '{0}' point is {1:N1}h old (limit {2}h), so it predates the update being reverted. Restoring to it would revert unrelated changes. Re-run with a larger -MaxAgeHours if that point really is the one you want." -f $CheckpointDescription, $ageHours, $MaxCheckpointAgeHours)
        exit 1
    }
    Write-Output ("Restoring to '{0}' sequence {1}, created {2:yyyy-MM-dd HH:mm} ({3:N1}h ago)." -f $CheckpointDescription, $candidate.SequenceNumber, $created, $ageHours)
} else {
    Write-Output "Restoring to '$CheckpointDescription' sequence $($candidate.SequenceNumber) (creation time unreadable)."
}

try {
    # This reboots the machine to apply the restore, so nothing after it is
    # guaranteed to run.
    Restore-Computer -RestorePoint $candidate.SequenceNumber -Confirm:$false -ErrorAction Stop
    Write-Output "Restore initiated - the machine will restart to complete it."
    exit 0
} catch {
    Write-Output "ROLLBACK FAILED: $($_.Exception.Message)"
    exit 1
}
