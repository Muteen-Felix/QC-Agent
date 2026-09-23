# scripts/repeat.ps1 - run the orchestrator N times and summarize each run. ASCII ONLY (see env.ps1).
#   .\scripts\repeat.ps1 -Times 3 [-Only "t-101,t-canary-01"] [-Plan plan.yaml]
# Exit 0 only if EVERY run produced valid QRS files and zero results with status=error.
param([int]$Times = 3, [string]$Only = "", [string]$Plan = "plan.yaml")

# plan.yaml and scripts/toyapp.ps1 use the demo SUT at this default endpoint.
$env:APP_BASE_URL = "http://127.0.0.1:8000"

function LastRun {
    $runs = @(
        Get-ChildItem "runs" -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^r-\d{4}$" } |
        Sort-Object { [int]$_.Name.Substring(2) }
    )
    if ($runs.Count -eq 0) { return $null }
    return $runs[-1]
}

$bad = 0
for ($i = 1; $i -le $Times; $i++) {
    $previous = LastRun
    $argv = @("orchestrator.py", "--plan", $Plan)
    if ($Only -ne "") { $argv += @("--only", $Only) }
    & python @argv | Out-Null
    $code = $LASTEXITCODE
    $latest = LastRun
    if ($null -eq $latest -or ($null -ne $previous -and $latest.Name -eq $previous.Name)) {
        "run {0}: orchestrator_exit={1} valid_qrs=False error_results=? dir=none" -f $i, $code
        $bad++
        continue
    }
    $run = $latest.FullName
    $files = @(Get-ChildItem (Join-Path (Join-Path $run "results") "*.json") | ForEach-Object { $_.FullName })
    if ($files.Count -eq 0) {
        "run {0}: orchestrator_exit={1} valid_qrs=False error_results=? dir={2}" -f $i, $code, $latest.Name
        $bad++
        continue
    }
    & python (Join-Path "tools" "validate.py") result @files | Out-Null
    $valid = ($LASTEXITCODE -eq 0)
    $errors = @($files | Where-Object { (Get-Content $_ -Raw -Encoding UTF8 | ConvertFrom-Json).status -eq "error" }).Count
    "run {0}: orchestrator_exit={1} valid_qrs={2} error_results={3} dir={4}" -f $i, $code, $valid, $errors, $latest.Name
    if ((-not $valid) -or ($errors -gt 0)) { $bad++ }
}
if ($bad -eq 0) { "ALL $Times RUNS OK"; exit 0 }
"$bad of $Times runs had invalid or error results"
exit 1
