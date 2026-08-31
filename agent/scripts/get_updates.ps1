# Collects pending updates from Windows Update (OS/security patches) and winget (third-party apps).

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-WindowsUpdates {
    try {
        $session  = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $result   = $searcher.Search("IsInstalled=0 and IsHidden=0")

        $updates = @()
        foreach ($u in $result.Updates) {
            # KBArticleIDs is a COM collection
            $kbs = @()
            foreach ($kb in $u.KBArticleIDs) { $kbs += "KB$kb" }

            $updates += [pscustomobject]@{
                name              = $u.Title
                kb                = ($kbs -join ", ")
                severity          = $u.MsrcSeverity
                current_version   = $null
                available_version = $null
            }
        }
        return $updates
    } catch {
        return @()
    }
}

function Get-WingetUpgrades {
    try {
        # Resolved rather than invoked as "winget": under LOCAL SYSTEM the PATH
        # alias lives in a user profile that does not exist. See _winget.ps1.
        . "$PSScriptRoot\_winget.ps1"
        $winget = Resolve-Winget
        if (-not $winget) { return @() }

        # Use --accept-source-agreements for the edge case where OpenPatch runs
        # on a machine where the msstore terms have never been accepted
        $raw = & $winget upgrade --include-unknown --disable-interactivity `
                                --accept-source-agreements 2>&1 | Out-String
        $lines = $raw -split "`r?`n"

        $headerIndex = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^Name\s+Id\s+Version\s+Available\s+Source') {
                $headerIndex = $i
                break
            }
        }
        if ($headerIndex -lt 0) { return @() }

        $header   = $lines[$headerIndex]
        $idPos    = $header.IndexOf("Id")
        $verPos   = $header.IndexOf("Version")
        $availPos = $header.IndexOf("Available")
        $srcPos   = $header.IndexOf("Source")
        if ($idPos -lt 0 -or $verPos -lt 0 -or $availPos -lt 0 -or $srcPos -lt 0) { return @() }

        function Get-Field([string]$line, [int]$start, [int]$end) {
            if ($line.Length -le $start) { return "" }
            $stop = [Math]::Min($end, $line.Length)
            return $line.Substring($start, $stop - $start).Trim()
        }

        $updates = @()
        for ($i = $headerIndex + 1; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            if ($line -match '^-+$') { continue }            # separator rule
            if ($line -match '^\d+ upgrades? available') { continue }  # trailing summary
            if ($line -match 'require explicit targeting') { continue }

            $name = Get-Field $line 0 $idPos
            if ([string]::IsNullOrWhiteSpace($name)) { continue }

            $updates += [pscustomobject]@{
                name              = $name
                kb                = Get-Field $line $idPos $verPos      # package id
                severity          = $null
                current_version   = Get-Field $line $verPos $availPos
                available_version = Get-Field $line $availPos $srcPos
            }
        }
        return $updates
    } catch {
        return @()
    }
}

$payload = [pscustomobject]@{
    windows = @(Get-WindowsUpdates)
    winget  = @(Get-WingetUpgrades)
}

# -Depth guards to avoid nested pscustomobjects being truncated to strings.
$payload | ConvertTo-Json -Depth 5 -Compress
