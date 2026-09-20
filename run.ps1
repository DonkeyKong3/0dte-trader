# One-command local run for Windows PowerShell.
# Creates the venv on first run, installs/updates deps, then starts the
# dashboard bound to all network interfaces so it's reachable from your
# phone over the same Wi-Fi.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    py -m venv .venv
}

Write-Host "Installing/updating dependencies..."
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -notmatch "Loopback" -and $_.IPAddress -notmatch "^169\." } |
    Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "Starting dashboard:"
Write-Host "  This PC:  http://localhost:8000"
if ($ip) {
    Write-Host "  Phone/LAN: http://${ip}:8000   (same Wi-Fi, e.g. from your iPhone)"
}
Write-Host "  Press Ctrl+C to stop."
Write-Host ""

& .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
