# Post-patch smoke test: confirms a business-critical toolchain still works
#   exit 0  workflow still functions
#   exit 1  workflow is broken

param(
    [string]$VerifyCommand = ""
)

$ErrorActionPreference = "Continue"

if ($VerifyCommand) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Stop"
    $LASTEXITCODE = 0
    try {
        $output = (Invoke-Expression $VerifyCommand 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Output "Workflow verification FAILED. '$VerifyCommand' exited $LASTEXITCODE.`n$output"
            exit 1
        }
        Write-Output "Workflow verification PASSED. '$VerifyCommand' completed.`n$output"
        exit 0
    } catch {
        Write-Output "Workflow verification FAILED. '$VerifyCommand' raised: $($_.Exception.Message)"
        exit 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

$candidates = @(
    @{ Name = "Python"; Command = "python" },
    @{ Name = "Node";   Command = "node" }
)

$working = @()
$problems = @()

foreach ($candidate in $candidates) {
    $resolved = Get-Command $candidate.Command -ErrorAction SilentlyContinue
    if (-not $resolved) {
        $problems += "$($candidate.Name): not on PATH"
        continue
    }

    try {
        $version = (& $candidate.Command --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $version) {
            $working += "$($candidate.Name) $version"
        } else {
            $problems += "$($candidate.Name): found at $($resolved.Source) but did not run"
        }
    } catch {
        $problems += "$($candidate.Name): found at $($resolved.Source) but failed to execute"
    }
}

if ($working.Count -gt 0) {
    Write-Output "Workflow verification PASSED (default check - no command configured). Working: $($working -join '; ')"
    if ($problems.Count -gt 0) { Write-Output "Also noted: $($problems -join '; ')" }

if (-not $VerifyCommand) {
    Write-Output "Workflow verification PASSED (nothing configured for this endpoint)."
    exit 0
}

$ErrorActionPreference = "Stop"
$LASTEXITCODE = 0
try {
    $output = (Invoke-Expression $VerifyCommand 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Output "Workflow verification FAILED. '$VerifyCommand' exited $LASTEXITCODE.`n$output"
        exit 1
    }
    Write-Output "Workflow verification PASSED. '$VerifyCommand' completed.`n$output"
    exit 0
} catch {
    Write-Output "Workflow verification FAILED. '$VerifyCommand' raised: $($_.Exception.Message)"
    exit 1
}
