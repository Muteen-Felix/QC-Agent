# scripts/dashboard.ps1 - start the QC-Agent web dashboard. ASCII ONLY (see env.ps1).
#   .\scripts\dashboard.ps1 [-Port 8080]
param([int]$Port = 8080)
. .\scripts\env.ps1
Write-Host ("QC-Agent dashboard: http://127.0.0.1:" + $Port) -ForegroundColor Cyan
if ($env:ALERT_WEBHOOK_URL) {
    Write-Host "ALERT_WEBHOOK_URL: set (webhook alerts on)" -ForegroundColor Green
} else {
    Write-Host "ALERT_WEBHOOK_URL: not set (alerts log-only, see dashboard/_state/alerts.log)" -ForegroundColor Yellow
}
try {
    $probe = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8000/__qc/config"
    Write-Host "SUT :8000 online" -ForegroundColor Green
} catch {
    Write-Host "SUT :8000 offline - run .\scripts\toyapp.ps1 start before pressing the trigger button" -ForegroundColor Yellow
}
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port $Port
