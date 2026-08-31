# Creates the pre-update checkpoint that rollback.ps1 restores to.
# This solves the problem where Windows silently refuses to create a
# restore point if one was created in the previous 24 hours.

$script:RestoreKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
$script:ThrottleValueName = "SystemRestorePointCreationFrequency"


function Get-RestorePointThrottle {
    # Returns the configured throttle in minutes, or $null when the value is
    # absent - which is the stock state and means "24 hours".
    try {
        $item = Get-ItemProperty -Path $script:RestoreKey -Name $script:ThrottleValueName -ErrorAction Stop
        return [int]$item.$($script:ThrottleValueName)
    } catch {
        return $null
    }
}


function Set-RestorePointThrottle {
    param([Parameter(Mandatory)][int]$Minutes)
    if (-not (Test-Path $script:RestoreKey)) {
        New-Item -Path $script:RestoreKey -Force | Out-Null
    }
    New-ItemProperty -Path $script:RestoreKey -Name $script:ThrottleValueName `
                     -Value $Minutes -PropertyType DWord -Force | Out-Null
}


function Restore-RestorePointThrottle {
    param([AllowNull()][object]$Previous)
    if ($null -eq $Previous) {
        Remove-ItemProperty -Path $script:RestoreKey -Name $script:ThrottleValueName `
                            -ErrorAction SilentlyContinue
    } else {
        Set-RestorePointThrottle -Minutes ([int]$Previous)
    }
}


function Get-LatestRestorePointSequence {
    $points = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue)
    return ($points | Sort-Object SequenceNumber -Descending | Select-Object -First 1).SequenceNumber
}


function New-OpenPatchCheckpoint {
    <#
        Creates the pre-update checkpoint and reports what actually happened.
        -ThrottleMinutes  0  create a checkpoint however recently the last one
                             was made (the default, and what makes revert
                             dependable)
                          N  allow one only if none was created in the last N
                             minutes
                         -1  leave the machine's own setting alone entirely
    #>
    param(
        [string]$Description = "OpenPatch Pre-Update",
        [int]$ThrottleMinutes = 0
    )

    $before = Get-LatestRestorePointSequence
    $previousThrottle = $null
    $adjusted = $false

    try {
        if ($ThrottleMinutes -ge 0) {
            $previousThrottle = Get-RestorePointThrottle
            $current = if ($null -eq $previousThrottle) { 1440 } else { $previousThrottle }
            if ($current -ne $ThrottleMinutes) {
                Set-RestorePointThrottle -Minutes $ThrottleMinutes
                $adjusted = $true
            }
        }

        Checkpoint-Computer -Description $Description -RestorePointType 'MODIFY_SETTINGS' -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            Created = $false
            SequenceNumber = $null
            Message = "WARNING: could not create a restore point: $($_.Exception.Message)"
        }
    } finally {
        # In finally so an interrupted or failed checkpoint still leaves the
        # machine's restore-point policy as we found it.
        if ($adjusted) {
            try { Restore-RestorePointThrottle -Previous $previousThrottle } catch {
                Write-Output "WARNING: could not restore SystemRestorePointCreationFrequency to its previous value: $($_.Exception.Message)"
            }
        }
    }

    # Since Checkpoint-Computer reports success even when Windows skipped the
    # creation, the only trustworthy check is whether a new point exists.
    $after = Get-LatestRestorePointSequence
    if ($after -and $after -ne $before) {
        $note = if ($adjusted) { " (restore-point throttle relaxed for this checkpoint, then restored)" } else { "" }
        return [pscustomobject]@{
            Created = $true
            SequenceNumber = $after
            Message = "Restore point created: '$Description' (sequence $after)$note."
        }
    }

    $reason = if ($ThrottleMinutes -lt 0) {
        "the machine's own SystemRestorePointCreationFrequency setting was left in place, and it skipped this one"
    } else {
        "Windows reported success but no new restore point exists"
    }
    return [pscustomobject]@{
        Created = $false
        SequenceNumber = $null
        Message = "WARNING: no restore point was created - $reason. Rollback would target an older point, so it will refuse to run."
    }
}
