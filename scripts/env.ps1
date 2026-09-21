# scripts/env.ps1 - dot-source it:   . .\scripts\env.ps1
# ASCII ONLY. Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI, so non-ASCII text can break parsing.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

if (Test-Path .env) {
    foreach ($line in Get-Content .env -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$' -and $line.TrimStart()[0] -ne '#') {
            $val = $Matches[2].Trim().Trim('"')
            if ($val -ne "") { Set-Item -Path ("Env:" + $Matches[1]) -Value $val }
        }
    }
}

$venvScripts = Join-Path (Get-Location) ".venv\Scripts"
if ((Test-Path $venvScripts) -and ($env:PATH -notlike "*$venvScripts*")) {
    $env:PATH = "$venvScripts;$env:PATH"
}
