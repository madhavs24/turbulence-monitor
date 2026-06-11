# Deploy Turbulence Monitor: GitHub push + Render Blueprint helper
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$RepoOwner = "madhavs24"
$RepoName = "turbulence-monitor"
$RemoteUrl = "https://github.com/$RepoOwner/$RepoName.git"
$RepoApi = "https://api.github.com/repos/$RepoOwner/$RepoName"
$NewRepoUrl = "https://github.com/new?name=$RepoName"

if (-not (Test-Path ".git")) {
    Write-Host "No git repo here. Run from turbulence-agent/ after git init."
    exit 1
}

git branch -M main | Out-Null
git remote remove origin 2>$null
git remote add origin $RemoteUrl

function Test-RepoExists {
    try {
        Invoke-RestMethod -Uri $RepoApi -Headers @{"User-Agent" = "turbulence-deploy"} | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-RepoExists)) {
    Write-Host ""
    Write-Host "Step 1 - Create the GitHub repo (browser will open)."
    Write-Host "  Name: $RepoName"
    Write-Host "  Owner: $RepoOwner"
    Write-Host "  Leave it EMPTY (no README, .gitignore, or license)."
    Write-Host ""
    Start-Process $NewRepoUrl
    Write-Host "Waiting for repo to appear..."
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        if (Test-RepoExists) { break }
        Start-Sleep -Seconds 5
    }
    if (-not (Test-RepoExists)) {
        Write-Host "Repo not found yet. Create it in the browser, then run: git push -u origin main"
        exit 1
    }
}

Write-Host "Pushing to $RemoteUrl ..."
git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Push failed. Sign in to GitHub if prompted, then run: git push -u origin main"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "GitHub push complete."
Write-Host ""
Write-Host "Step 2 - Deploy on Render (browser will open)."
Write-Host "  1. Sign in with GitHub"
Write-Host "  2. New + -> Blueprint"
Write-Host "  3. Select repo: $RepoName"
Write-Host "  4. Apply / Create (reads render.yaml automatically)"
Write-Host ""
Start-Process "https://dashboard.render.com/blueprint/new"
Write-Host "When Render shows Live, open: https://$RepoName.onrender.com"
