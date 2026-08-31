# Locates winget.exe which  sits under Program Files\WindowsApps.

function Resolve-Winget {
    # Winget alias is on PATH for a normal user and is what should be used.
    $onPath = Get-Command winget -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    # Look in Windows where the package is installed.
    foreach ($allUsers in @($false, $true)) {
        try {
            $package = if ($allUsers) {
                Get-AppxPackage -AllUsers Microsoft.DesktopAppInstaller -ErrorAction Stop
            } else {
                Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction Stop
            }
            $package = $package | Sort-Object Version -Descending | Select-Object -First 1
            if ($package -and $package.InstallLocation) {
                $candidate = Join-Path $package.InstallLocation "winget.exe"
                if (Test-Path $candidate) { return $candidate }
            }
        } catch {
            # -AllUsers needs elevation
        }
    }

    # Look under WindowsApps directly.
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($root in $roots) {
        $packages = Join-Path $root "WindowsApps"
        if (-not (Test-Path $packages)) { continue }

        $match = Get-ChildItem -Path $packages -Filter "Microsoft.DesktopAppInstaller_*_x64__8wekyb3d8bbwe" `
                               -Directory -ErrorAction SilentlyContinue |
                 Sort-Object Name -Descending |
                 ForEach-Object { Join-Path $_.FullName "winget.exe" } |
                 Where-Object { Test-Path $_ } |
                 Select-Object -First 1

        if ($match) { return $match }
    }

    return $null
}
