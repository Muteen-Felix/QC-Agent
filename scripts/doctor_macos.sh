#!/usr/bin/env bash
# macOS counterpart to doctor.ps1. Run from any directory; checks the repo environment.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

BAD=0
PY="$ROOT/.venv/bin/python"
FP_PYTHON=""
FP_NODE_MAJOR=""
FP_K6_PIN=""
FREEZE=""

ok() { printf 'OK   %-11s %s\n' "$1" "$2"; }
fail() {
    printf 'FAIL %-11s %s\n' "$1" "$2"
    BAD=$((BAD + 1))
}

if [[ -x "$PY" ]]; then
    if value=$("$PY" --version 2>&1); then
        wanted=$(tr -d '\r\n' < .python-version)
        if [[ "$value" == *"Python $wanted"* ]]; then
            FP_PYTHON="Python $wanted"
            ok python "$value"
        else
            fail python "need Python $wanted, got $value"
        fi
    else
        fail python "$value"
    fi
else
    fail python ".venv/bin/python missing (create the project virtualenv)"
fi

if command -v node >/dev/null 2>&1; then
  if value=$(node --version 2>&1); then
    value="${value#v}"
    wanted_major=$(tr -d '[:space:]' < .node-version)
    if [[ "$value" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        actual_major="${BASH_REMATCH[1]}"
        minor="${BASH_REMATCH[2]}"
        major_num=$((10#$actual_major))
        minor_num=$((10#$minor))
        if [[ "$actual_major" == "$wanted_major" ]] && \
            (( major_num > 22 || (major_num == 22 && minor_num >= 12) )); then
            FP_NODE_MAJOR="$wanted_major"
            ok node "v$value"
        else
            fail node "need major $wanted_major and >= 22.12, got $value"
        fi
    else
        fail node "unrecognized version: $value"
    fi
  else
      fail node "$value"
  fi
else
    fail node "node command not found"
fi

k6_pin=$(tr -d '\r\n' < .k6-version)
if command -v k6 >/dev/null 2>&1; then
  if value=$(k6 version 2>&1); then
    wanted_k6=$(printf '%s' "$k6_pin" | grep -Eo 'v[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true)
    if [[ -n "$wanted_k6" && "$value" == *"$wanted_k6"* ]]; then
        FP_K6_PIN="$k6_pin"
        ok k6 "$k6_pin"
    else
        fail k6 "need $k6_pin, got: $value"
    fi
  else
      fail k6 "$value"
  fi
else
    fail k6 "k6 command not found"
fi

if [[ -x "$ROOT/.venv/bin/st" ]]; then
    if value=$("$ROOT/.venv/bin/st" --version 2>&1); then
        ok st "$value"
    else
        fail st "$value"
    fi
else
    fail st ".venv/bin/st missing"
fi

if [[ -x "$PY" ]]; then
  if value=$("$PY" -c "import deepeval; print('import ok')" 2>&1); then
      ok deepeval "$value"
  else
      fail deepeval "$value"
  fi
else
    fail deepeval ".venv/bin/python missing"
fi

if [[ -x "$PY" ]]; then
  if value=$("$PY" -c "import jsonschema, yaml, fastapi, uvicorn, httpx, pytest; print('import ok')" 2>&1); then
      ok libs "$value"
  else
      fail libs "$value"
  fi
else
    fail libs ".venv/bin/python missing"
fi

if [[ -d "$ROOT/node_modules/@midscene/cli" ]]; then
    ok midscene installed
else
    fail midscene "node_modules/@midscene/cli missing (run npm ci)"
fi

if [[ -f "$ROOT/.env" ]]; then
    ok .env present
else
    fail .env ".env missing (copy .env.example)"
fi

if [[ -x "$PY" ]]; then
  if value=$("$PY" -m pip freeze 2>&1); then
      FREEZE="$value"
      if [[ -n "$FREEZE" ]]; then
          package_count=$(printf '%s\n' "$FREEZE" | awk 'END { print NR }')
      else
          package_count=0
      fi
      ok pip-freeze "$package_count packages"
    else
      fail pip-freeze "$value"
    fi
else
    fail pip-freeze ".venv/bin/python missing"
fi

if (( BAD == 0 )); then
    freeze_file=$(mktemp "${TMPDIR:-/tmp}/qcagent-freeze.XXXXXX") || {
        fail fingerprint "could not create temporary freeze file"
        exit 1
    }
    printf '%s' "$FREEZE" > "$freeze_file"
    if hex=$("$PY" "$ROOT/scripts/fingerprint.py" \
        --python-version "$FP_PYTHON" \
        --node-major "$FP_NODE_MAJOR" \
        --k6-pin "$FP_K6_PIN" \
        --pip-freeze-file "$freeze_file" 2>&1) && [[ "$hex" =~ ^[0-9a-f]{12}$ ]]; then
        printf 'ENV-FINGERPRINT %s   (compare this value across machines)\n' "$hex"
    else
        fail fingerprint "$hex"
        rm -f "$freeze_file"
        exit 1
    fi
    rm -f "$freeze_file"
else
    printf '%s check(s) FAILED\n' "$BAD"
    exit 1
fi
