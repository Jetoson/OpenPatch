# Restarts the endpoint on behalf of a queued RESTART task.
# shutdown returns as soon as the restart is scheduled, so the agent can report
# the task as succeeded before the machine actually goes down.

shutdown /r /t 60 /c "OpenPatch: a restart was requested by your administrator to finish applying updates. Please save your work."

if ($LASTEXITCODE -ne 0) {
    Write-Error "shutdown returned exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Output "Restart scheduled in 60 seconds."
