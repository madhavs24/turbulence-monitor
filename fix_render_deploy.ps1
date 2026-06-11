# Fix Render serving the wrong app (Turbulence v3 instead of Market Turbulence Monitor)
$ErrorActionPreference = "Stop"
$Url = "https://madhav-turbulence-monitor.onrender.com"

Write-Host ""
Write-Host "=== Render deploy mismatch fix ==="
Write-Host ""
Write-Host "Old URL turbulence-monitor.onrender.com may still serve Turbulence v3."
Write-Host "New service URL: $Url (Market Turbulence Monitor from madhavs24/turbulence-monitor)."
Write-Host ""

function Test-CorrectApp {
    try {
        $h = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 20
        return ($h.app -eq "market-turbulence-monitor")
    } catch {
        return $false
    }
}

if (Test-CorrectApp) {
    Write-Host "Correct app is already live at $Url"
    exit 0
}

Write-Host "Steps in Render dashboard (browser will open):"
Write-Host "  1. Open your turbulence-monitor service"
Write-Host "  2. Settings -> Build and Deploy:"
Write-Host "       Repository: madhavs24/turbulence-monitor"
Write-Host "       Branch: main"
Write-Host "       Runtime: Docker"
Write-Host "       Root Directory: (leave blank)"
Write-Host "  3. If repo is correct but app is still v3, you likely have an OLD manual deploy."
Write-Host "     Delete this service and recreate via New + -> Blueprint -> madhavs24/turbulence-monitor"
Write-Host "  4. Manual Deploy -> Deploy latest commit"
Write-Host "  5. Wait for Live, then run: python -m tests.verify_deploy"
Write-Host ""
Start-Process "https://dashboard.render.com"
Write-Host "Polling $Url every 30s for up to 10 minutes..."
$deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt $deadline) {
    if (Test-CorrectApp) {
        Write-Host "SUCCESS: correct app detected."
        python -m tests.verify_deploy
        exit $LASTEXITCODE
    }
    Start-Sleep -Seconds 30
}
Write-Host "Still wrong app. Complete the Render dashboard steps above, then run:"
Write-Host "  python -m tests.verify_deploy"
exit 1
