# Installs the OpenPatch agent as a scheduled task running under LOCAL SYSTEM
# at boot.

param(
    [switch]$Uninstall,
    [string]$TaskName = "OpenPatch Agent",
    [string]$PythonExe,

    # Full path to openpatch-agent.exe. Passed by the agent when it installs
    # itself.
    [string]$AgentExe
)

$ErrorActionPreference = "Stop"

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Elevated)) {
    Write-Error "This must be run from an elevated PowerShell."
    exit 1
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed scheduled task '$TaskName'."
    } else {
        Write-Output "No scheduled task named '$TaskName' is installed."
    }
    exit 0
}


$agentDir = Split-Path -Parent $PSScriptRoot
if ($AgentExe) {
    if (-not (Test-Path $AgentExe)) {
        Write-Error "The agent executable was not found at $AgentExe"
        exit 1
    }
    $agentExe = (Resolve-Path $AgentExe).Path
} else {
    $exeCandidates = @(
        (Join-Path $PSScriptRoot "openpatch-agent.exe"),
        (Join-Path $agentDir "openpatch-agent.exe"),
        (Join-Path (Split-Path -Parent $agentDir) "dist\openpatch-agent.exe")
    )
    $agentExe = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

$entryPoint = Join-Path $agentDir "agent_service.py"
if (-not $agentExe -and -not (Test-Path $entryPoint)) {
    Write-Error "Found neither openpatch-agent.exe nor agent_service.py near $agentDir"
    exit 1
}

if (-not $agentExe) {
    if (-not $PythonExe) {
        $candidate = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
        if (-not $candidate) { $candidate = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
        $PythonExe = $candidate
    }
    if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
        Write-Error "Could not find a Python interpreter. Pass one with -PythonExe C:\path\to\pythonw.exe"
        exit 1
    }
}

# The packaged agent keeps its config beside the executable.
$configDir = if ($agentExe) { Split-Path -Parent $agentExe } else { $agentDir }
$configPath = Join-Path $configDir "config.ini"
if (-not (Test-Path $configPath)) {
    Write-Warning "This device is not enrolled yet ($configPath is missing)."
    Write-Warning "The task will start and exit immediately until it is. Enrol it with:"
    if ($agentExe) {
        Write-Warning "    $agentExe enroll --server https://your-server:8000"
    } else {
        Write-Warning "    python agent_service.py enroll --server https://your-server:8000"
    }
}

if ($agentExe) {
    Write-Output "Agent  : $agentExe (packaged)"
    $action = New-ScheduledTaskAction -Execute $agentExe `
                                      -WorkingDirectory (Split-Path -Parent $agentExe)
} else {
    Write-Output "Python : $PythonExe"
    Write-Output "Agent  : $entryPoint (source)"
    $action = New-ScheduledTaskAction -Execute $PythonExe `
                                      -Argument "`"$entryPoint`"" `
                                      -WorkingDirectory $agentDir
}

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
                                        -LogonType ServiceAccount `
                                        -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Output "Replacing the existing '$TaskName' task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
                       -Action $action `
                       -Trigger $trigger `
                       -Principal $principal `
                       -Settings $settings `
                       -Description "OpenPatch RMM agent. Runs as SYSTEM" | Out-Null

Write-Output ""
Write-Output "Installed '$TaskName' (SYSTEM, highest privileges, starts at boot)."
Write-Output ""
Write-Output "Start it now:      Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Check it:          Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Output "Remove it:         powershell -File scripts\install_agent_task.ps1 -Uninstall"
Write-Output ""
Write-Output "The agent runs elevated as SYSTEM, which is what lets it install updates"
Write-Output "silently, take restore points, revert a bad patch and restart the machine non-interactively"
