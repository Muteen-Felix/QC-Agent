# scripts/toyapp.ps1 - start | stop | status of the noteboard toy app. ASCII ONLY.
#   .\scripts\toyapp.ps1 start [-Bugs "1,2,3"] [-LatencyMs 0] [-Port 8000]
#   .\scripts\toyapp.ps1 stop
#   .\scripts\toyapp.ps1 status        (exit 1 if not running)
param(
    [Parameter(Mandatory = $true)][ValidateSet("start", "stop", "status")][string]$Action,
    [string]$Bugs = "1,2,3",
    [int]$LatencyMs = 0,
    [int]$Port = 8000
)
$pidFile = ".toyapp.pid"

function Get-Live {
    if (Test-Path $pidFile) {
        $p = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
        if ($p) { return $p }
    }
    return $null
}

switch ($Action) {
    "stop" {
        $p = Get-Live
        if ($p) { Stop-Process -Id $p.Id -Force }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        "stopped"
    }
    "status" {
        $p = Get-Live
        if ($p) { "running pid=$($p.Id)" } else { "not running"; exit 1 }
    }
    "start" {
        if (Get-Live) { & $PSCommandPath -Action stop | Out-Null }
        New-Item -ItemType Directory -Force runs | Out-Null
        $env:QC_BUGS = $Bugs
        $env:QC_LATENCY_MS = "$LatencyMs"
        $py = (Resolve-Path ".venv\Scripts\python.exe").Path
        $p = Start-Process -FilePath $py -PassThru -WindowStyle Hidden `
            -ArgumentList "-m", "uvicorn", "toyapp.app:app", "--host", "127.0.0.1", "--port", "$Port" `
            -RedirectStandardOutput "runs\toyapp.out.log" -RedirectStandardError "runs\toyapp.err.log"
        $p.Id | Out-File -Encoding ascii $pidFile
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/openapi.json"; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch { }
        }
        if (-not $ok) { Write-Host "toyapp did not come up in 15s - see runs\toyapp.err.log"; exit 1 }
        "started pid=$($p.Id) port=$Port bugs=$Bugs latency_ms=$LatencyMs"
    }
}
