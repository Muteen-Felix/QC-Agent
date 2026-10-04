# Chuẩn bị môi trường đo Ground-Truth cho SUT "vahan-automation" (api-server). Chạy từ gốc repo QC-Agent:
#     powershell -ExecutionPolicy Bypass -File eval\vahan\prepare.ps1
# Không ghi gì vào repo SUT. Kết quả nằm trong runs\ (đã gitignore):
#   runs\vahan-sut\   bản snapshot ĐÚNG commit (git archive), bỏ tests\ và .venv: đây vừa là sut_root agent đọc, vừa là nguồn để chèn mutant
#   runs\vahan-venv\  venv chỉ chứa dependency của SUT (không cài SUT ở chế độ editable, để `app` luôn lấy từ bản sao đã chèn lỗi)
param([string]$Sut = "C:\Dev\personal\vahan-automation", [string]$Python = "python")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runs = Join-Path $root "runs"
$stage = Join-Path $runs "vahan-src"
$snap = Join-Path $runs "vahan-sut"
$venv = Join-Path $runs "vahan-venv"

$sha = (git -C $Sut rev-parse HEAD).Trim()
$dirty = git -C $Sut status --porcelain -- apps/api-server
if ($dirty) { Write-Warning "apps/api-server có thay đổi chưa commit (snapshot chỉ lấy phần ĐÃ commit):`n$dirty" }

Remove-Item $stage, $snap -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null
$tar = Join-Path $runs "vahan-src.tar"
git -C $Sut archive --format=tar -o $tar HEAD apps/api-server
tar -xf $tar -C $stage
Remove-Item $tar
Move-Item (Join-Path $stage "apps\api-server") $snap
Remove-Item $stage -Recurse -Force
# Test của dev nằm trong sandbox đọc của agent (deny-list không chặn `tests/`): bỏ đi để agent suy ra TC từ PRD + mã, không chép assertion có sẵn.
Remove-Item (Join-Path $snap "tests") -Recurse -Force -ErrorAction SilentlyContinue
# HỒ SƠ ĐO (lab profile): tắt bước kiểm auth trong BẢN SAO. Runtime test Ground-Truth chưa hỗ trợ auth động (docs/groundtruth-real-sut.md mục 1.1) và agent đọc mã nguồn, nên nếu middleware đòi
# Bearer thì agent (đúng luật) từ chối sinh test cho mọi endpoint. Cách tạm theo tài liệu: "tắt auth cho môi trường đo". Chỉ sửa runs\vahan-sut; repo SUT thật không đổi.
# Hệ quả ghi vào báo cáo: các AC về 401/503 do middleware (AC-1.4, 1.6, 1.8) không đo được trong hồ sơ này.
$patch = @'
import sys
from pathlib import Path
path = Path(sys.argv[1]) / "app" / "main.py"
text = path.read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"
old = nl.join(['    if not settings.ui_auth_configured:',
               '        return JSONResponse(',
               '            status_code=503,',
               '            content={"detail": "Authentication is not configured on the API server."},',
               '        )',
               '    return JSONResponse(',
               '        status_code=401,',
               '        content={"detail": "Authentication required or access token is invalid."},',
               '        headers={"WWW-Authenticate": "Bearer"},',
               '    )'])
new = nl.join(['    # LAB PROFILE: authentication is disabled in the measurement environment; every request is treated as the configured operator.',
               '    request.state.authenticated_user = settings.ui_auth_username',
               '    return await call_next(request)'])
if text.count(old) != 1:
    sys.exit("lab patch: đoạn middleware không khớp đúng 1 lần (SUT đã đổi?)")
path.write_bytes(text.replace(old, new).encode("utf-8"))
'@
$patchFile = Join-Path $runs "lab_patch_auth.py"
Set-Content -Path $patchFile -Value $patch -Encoding utf8
& $Python $patchFile $snap
if ($LASTEXITCODE -ne 0) { throw "không vá được middleware của bản sao lab" }
Remove-Item $patchFile
Set-Content -Path (Join-Path $runs "vahan-sut.source") -Value "commit=$sha`nlab_profile=auth-disabled (app/main.py middleware)" -Encoding ascii

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { & $Python -m venv $venv }
$vpy = Join-Path $venv "Scripts\python.exe"
$deps = & $vpy -c "import tomllib,sys; print('\n'.join(tomllib.load(open(sys.argv[1],'rb'))['project']['dependencies']))" (Join-Path $snap "pyproject.toml")
& $vpy -m pip install --quiet --disable-pip-version-check @($deps)
Write-Host "OK  commit $sha -> $snap"
Write-Host "OK  venv   $vpy"
