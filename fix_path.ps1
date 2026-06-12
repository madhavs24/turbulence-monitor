# Refreshes PATH from registry so tools installed via winget (gh, node, netlify)
# are visible in terminals that were open before install.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) { Write-Host "PATH OK - gh: $($gh.Source)" } else { Write-Host "gh still not found; reinstall with: winget install GitHub.cli" }
