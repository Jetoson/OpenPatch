# Get list of softwares installed in the machine
$paths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
)

$software = Get-ItemProperty $paths -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -ne $null } |
    Select-Object @{Name="name";Expression={$_.DisplayName}},
                  @{Name="version";Expression={if ($_.DisplayVersion) {$_.DisplayVersion} else {"Unknown"}}},
                  @{Name="publisher";Expression={if ($_.Publisher) {$_.Publisher} else {"Unknown"}}} |
    Sort-Object name -Unique

# Convert to JSON
$software | ConvertTo-Json