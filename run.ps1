# Launch Turbulence Monitor on Windows (handles Python PATH + correct folder).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = $null
foreach ($cmd in @("python", "py")) {
    try {
        $v = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) { $python = $cmd; break }
    } catch {}
}
if (-not $python) {
    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $fallback) { $python = $fallback }
}
if (-not $python) {
    Write-Host "Python not found. Install from https://python.org or run: winget install Python.Python.3.12"
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Installing dependencies..."
    & $python -m pip install -r requirements.txt
}

Write-Host "Starting server at http://localhost:8000"
& $python serve.py
