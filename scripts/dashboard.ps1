# scripts/dashboard.ps1 - start the QC-Agent web dashboard. ASCII ONLY (see env.ps1).
#   .\scripts\dashboard.ps1 [-Port 8080]
param([int]$Port = 8080)
. .\scripts\env.ps1
Write-Host ("QC-Agent dashboard: http://127.0.0.1:" + $Port) -ForegroundColor Cyan
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port $Port
