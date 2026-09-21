# scripts/doctor.ps1 - environment check. Exit 0 = all OK. ASCII ONLY (see env.ps1).
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
$script:bad = 0
$script:fpPython = ""
$script:fpNodeMajor = ""
$script:fpK6Pin = ""
$script:fpPipFreeze = ""

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
Check "python" {
    $want = (Get-Content .python-version).Trim()
    $v = Run-Native $py @("--version")
    if ($v -notmatch [regex]::Escape("Python $want")) { throw "need Python $want, got $v" }
    $script:fpPython = "Python $want"
    $v
}
Check "node" {
    $v = (Run-Native "node" @("--version")).TrimStart("v")
    $major = [int](Get-Content .node-version).Trim()
    if ([version]$v -lt [version]"22.12" -or [int]$v.Split(".")[0] -ne $major) { throw "need major $major and >= 22.12, got $v" }
    $script:fpNodeMajor = "$major"
    "v$v"
}
Check "k6" {
    $want = (Get-Content .k6-version).Trim()
    $v = Run-Native "k6" @("version")
    $wantVersion = [regex]::Match($want, 'v\d+\.\d+\.\d+').Value
    if (-not $wantVersion -or $v -notmatch [regex]::Escape($wantVersion)) { throw "need k6 $want, got: $v" }
    $script:fpK6Pin = $want
    $want
}
Check "st"         { Run-Native ".\.venv\Scripts\st.exe" @("--version") }
Check "deepeval"   { Run-Native $py @("-c", "import deepeval; print('import ok')") }
Check "libs"       { Run-Native $py @("-c", "import jsonschema, yaml, fastapi, uvicorn, httpx, pytest; print('import ok')") }
Check "midscene"   { if (-not (Test-Path "node_modules\@midscene\cli")) { throw "node_modules\@midscene\cli missing (run npm ci)" }; "installed" }
Check ".env"       { if (-not (Test-Path .env)) { throw ".env missing (copy .env.example)" }; "present" }
Check "pip-freeze" {
    $freeze = (Run-Native $py @("-m", "pip", "freeze")) -replace "`r", ""
    $script:fpPipFreeze = $freeze
    "{0} packages" -f ($freeze -split "`n").Count
}

if ($script:bad -eq 0) {
    $freezeFile = [IO.Path]::GetTempFileName()
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($freezeFile, $script:fpPipFreeze, $utf8)
        $hex = Run-Native $py @("scripts/fingerprint.py", "--python-version", $script:fpPython,
            "--node-major", $script:fpNodeMajor, "--k6-pin", $script:fpK6Pin,
            "--pip-freeze-file", $freezeFile)
        if ($hex -notmatch '^[0-9a-f]{12}$') { throw "fingerprint helper returned invalid output: $hex" }
    }
    catch {
        Write-Host ("FAIL fingerprint {0}" -f $_.Exception.Message)
        Remove-Item $freezeFile -ErrorAction SilentlyContinue
        exit 1
    }
    Remove-Item $freezeFile -ErrorAction SilentlyContinue
    Write-Host ("ENV-FINGERPRINT {0}   (3 machines must print the same value)" -f $hex.Substring(0, 12))
    exit 0
}
Write-Host ("{0} check(s) FAILED" -f $script:bad)
exit 1
