"""Validate file JSON theo contract.
    python tools/validate.py result tests/fixtures/contract/result.*.json
    python tools/validate.py task   tests/fixtures/contract/task.*.json
Exit 0 = tất cả PASS · 1 = có file FAIL.
"""
import json, pathlib, sys

from jsonschema import Draft202012Validator

SCHEMAS = {"result": "schemas/result.json", "task": "schemas/task_spec.json"}


def main(kind, paths):
    schema = json.loads(pathlib.Path(SCHEMAS[kind]).read_text(encoding="utf-8-sig"))
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    bad = 0
    for p in paths:
        obj = json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))
        errs = sorted(v.iter_errors(obj), key=lambda e: list(map(str, e.path)))
        if errs:
            bad += 1
            print(f"FAIL {p}")
            for e in errs:
                print("   -", "/".join(map(str, e.path)) or "<root>", ":", e.message[:160])
        else:
            print(f"PASS {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SCHEMAS:
        sys.exit("dùng: python tools/validate.py <result|task> file...")
    sys.exit(main(sys.argv[1], sys.argv[2:]))
