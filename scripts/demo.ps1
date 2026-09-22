# scripts/demo.ps1 - scripted demo (timeline of R5 5.2). ASCII ONLY (see env.ps1).
#   .\scripts\demo.ps1                interactive: press Enter between stages
#   .\scripts\demo.ps1 -Auto          no prompts (used for the timed dry-run, STEP 57)
#   .\scripts\demo.ps1 -Fast          run only the GATE tasks live; show the discovery lane from the RECORDED run
# Prerequisite: toy app running with the seeded bugs ON:  .\scripts\toyapp.ps1 start
param([switch]$Auto, [switch]$Fast, [string]$Plan = "plan.yaml")

# The scripted demo starts the toy SUT on its documented default port.
$env:APP_BASE_URL = "http://127.0.0.1:8000"
$rec = Join-Path "recordings" "demo-good-run"

function Stage([string]$title) {
    Write-Host ""
    Write-Host ("=== " + $title + " ===") -ForegroundColor Cyan
    if (-not $Auto) { Read-Host "Enter to run" | Out-Null }
}
function LastRun {
    $runs = @(
        Get-ChildItem "runs" -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^r-\d{4}$" } |
        Sort-Object { [int]$_.Name.Substring(2) }
    )
    if ($runs.Count -eq 0) { return $null }
    return $runs[-1].FullName
}
function GateIds([string]$runDir) {
    ((Get-Content (Join-Path $runDir "report.json") -Raw -Encoding UTF8 | ConvertFrom-Json).deterministic_view | ForEach-Object { $_.task_id }) -join ","
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$marks = @()

Stage "1. plan.yaml is COMMITTED. CI runs exactly this file. No LLM decides what runs."
Get-Content $Plan -Encoding UTF8 -TotalCount 40
$marks += ("stage1 {0:mm\:ss}" -f $sw.Elapsed)

Stage "2. run the orchestrator (safe workers in parallel; k6 and Midscene run alone)"
if ($Fast) {
    Write-Host "(-Fast: gate tasks run LIVE; the discovery lane is shown from the RECORDED run in $rec)" -ForegroundColor Yellow
    python orchestrator.py --plan $Plan --only (GateIds $rec)
    $show = $rec
} else {
    python orchestrator.py --plan $Plan
    $show = $null
}
"orchestrator exit code = $LASTEXITCODE"
$r1 = LastRun
if (-not $show) { $show = $r1 }
$marks += ("stage2 {0:mm\:ss}" -f $sw.Elapsed)

Stage "3. report: three sections that NEVER add up into one pass-rate"
Get-Content (Join-Path $show "report.md") -Encoding UTF8
$marks += ("stage3 {0:mm\:ss}" -f $sw.Elapsed)

Stage "4. one finding was detected by a deterministic signal -> promote candidate"
Select-String -Path (Join-Path $show "report.md") -Pattern "dom_unchanged|promote" | ForEach-Object { $_.Line }
$marks += ("stage4 {0:mm\:ss}" -f $sw.Elapsed)

Stage "5. run the GATE tasks AGAIN (same commit), then diff the deterministic part -> must be IDENTICAL"
python orchestrator.py --plan $Plan --only (GateIds $r1) | Out-Null
$r2 = LastRun
python (Join-Path "tools" "diff_runs.py") $r1 $r2
$marks += ("stage5 {0:mm\:ss}" -f $sw.Elapsed)

Stage "6. add a 5th worker: how many lines of core/ changed?"
$h = git log --grep="add mock2 worker" -1 --format=%h
git show --stat $h
$marks += ("stage6 {0:mm\:ss}" -f $sw.Elapsed)

Stage "7. canary: a task that MUST fail, run on the autonomous worker"
Select-String -Path (Join-Path $show "report.md") -Pattern "CANARY" | ForEach-Object { $_.Line }
$marks += ("stage7 {0:mm\:ss}" -f $sw.Elapsed)

Stage "8. mutation table: which seeded faults the gate catches"
Get-Content (Join-Path "docs" "mutants.md") -Encoding UTF8
$marks += ("stage8 {0:mm\:ss}" -f $sw.Elapsed)

Write-Host ""
$marks | ForEach-Object { Write-Host $_ }
"TOTAL elapsed {0:mm\:ss}" -f $sw.Elapsed
