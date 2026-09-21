# scripts/bootstrap.ps1 - ONE command to build the environment. ASCII ONLY (see env.ps1).
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
# (-ExecutionPolicy Bypass avoids "running scripts is disabled on this system" on a fresh Windows.)
$ErrorActionPreference = "Stop"
$want = (Get-Content .python-version).Trim()

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "== creating .venv with Python $want"
    & py "-$want" -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "py -$want failed. Install Python $want or change .python-version (decision D-03)." }
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip

$req = "requirements.txt"
if (Test-Path requirements.lock) { $req = "requirements.lock" }
Write-Host "== pip install -r $req"
& $py -m pip install -r $req
if ($LASTEXITCODE -ne 0) { throw "pip install failed - read the resolver message above (see Troubleshooting #4)" }

if (Test-Path package-lock.json) {
    Write-Host "== npm ci"
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
}

if (-not (Get-Command k6 -ErrorAction SilentlyContinue)) {
    Write-Host "== installing k6 with winget (afterwards open a NEW terminal so PATH refreshes)"
    winget install k6 --source winget --accept-package-agreements --accept-source-agreements
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "== created .env from .env.example - fill in the keys, then rerun doctor"
}

Write-Host "== doctor"
& powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
exit $LASTEXITCODE
