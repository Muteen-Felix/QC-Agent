# scripts/doctor.ps1 - environment check. Exit 0 = all OK. ASCII ONLY (see env.ps1).
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
$script:bad = 0

function Run-Native([string]$exe, [string[]]$argv) {
    $out = & $exe @argv 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$exe $($argv -join ' ') -> exit $LASTEXITCODE" }
    return $out.Trim()
}
function Check([string]$name, [scriptblock]$test) {
    try   { $r = & $test; Write-Host ("OK   {0,-11} {1}" -f $name, $r) }
    catch { Write-Host ("FAIL {0,-11} {1}" -f $name, $_.Exception.Message); $script:bad++ }
}

$py = ".\.venv\Scripts\python.exe"
$script:fp = ""

Check "python" {
    $want = (Get-Content .python-version).Trim()
    $v = Run-Native $py @("--version")
    if ($v -notmatch [regex]::Escape("Python $want")) { throw "need Python $want, got $v" }
    $script:fp += $v
    $v
}
Check "node" {
    $v = (Run-Native "node" @("--version")).TrimStart("v")
    $major = [int](Get-Content .node-version).Trim()
    if ([version]$v -lt [version]"22.12" -or [int]$v.Split(".")[0] -ne $major) { throw "need major $major and >= 22.12, got $v" }
    $script:fp += "node$major"
    "v$v"
}
Check "k6" {
    $want = (Get-Content .k6-version).Trim()
    $v = Run-Native "k6" @("version")
    $wantVersion = [regex]::Match($want, 'v\d+\.\d+\.\d+').Value
    if (-not $wantVersion -or $v -notmatch [regex]::Escape($wantVersion)) { throw "need k6 $want, got: $v" }
    $script:fp += "k6$want"
    $want
}
Check "st"         { Run-Native ".\.venv\Scripts\st.exe" @("--version") }
Check "deepeval"   { Run-Native $py @("-c", "import deepeval; print('import ok')") }
Check "libs"       { Run-Native $py @("-c", "import jsonschema, yaml, fastapi, uvicorn, httpx, pytest; print('import ok')") }
Check "midscene"   { if (-not (Test-Path "node_modules\@midscene\cli")) { throw "node_modules\@midscene\cli missing (run npm ci)" }; "installed" }
Check ".env"       { if (-not (Test-Path .env)) { throw ".env missing (copy .env.example)" }; "present" }
Check "pip-freeze" {
    $freeze = (Run-Native $py @("-m", "pip", "freeze")) -replace "`r", ""
    $lines = ($freeze -split "`n" | Sort-Object) -join "`n"
    $script:fp += $lines
    "{0} packages" -f ($freeze -split "`n").Count
}

if ($script:bad -eq 0) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hex = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:fp)) | ForEach-Object { $_.ToString("x2") }) -join ""
    Write-Host ("ENV-FINGERPRINT {0}   (3 machines must print the same value)" -f $hex.Substring(0, 12))
    exit 0
}
Write-Host ("{0} check(s) FAILED" -f $script:bad)
exit 1
