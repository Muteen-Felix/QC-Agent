"""Trích schemas/result.json + examples/*.json từ docs/architecture.md (nguồn duy nhất).
Chạy:  python tools/extract_from_arch.py [--arch docs/architecture.md]
"""
import argparse, json, pathlib, re, sys

def strip_jsonc(text: str) -> str:
    out = []
    for line in text.splitlines():
        # '//' chỉ là comment khi đứng đầu dòng hoặc đứng sau khoảng trắng (URL có '://' nên không dính)
        line = re.sub(r'(^|\s)//.*$', '', line)
        out.append(line)
    return "\n".join(out)

def fence_after(md: str, heading_regex: str, lang: str, nth: int = 0) -> list[str]:
    m = re.search(heading_regex, md, re.M)
    if not m:
        sys.exit(f"KHÔNG THẤY heading: {heading_regex}")
    tail = md[m.end():]
    nxt = re.search(r'^#{1,2} ', tail, re.M)          # dừng ở heading cấp 1-2 kế tiếp
    tail = tail[: nxt.start()] if nxt else tail
    return re.findall(rf'^```{lang}\n(.*?)^```', tail, re.M | re.S)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="docs/architecture.md")
    a = ap.parse_args()
    md = pathlib.Path(a.arch).read_text(encoding="utf-8")

    blocks = fence_after(md, r'^## 4\.1 ', 'jsonc')
    if len(blocks) != 1:
        sys.exit(f"Mục 4.1 phải có đúng 1 khối jsonc, thấy {len(blocks)}")
    schema = json.loads(strip_jsonc(blocks[0]))
    pathlib.Path("schemas").mkdir(exist_ok=True)
    pathlib.Path("schemas/result.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ex = fence_after(md, r'^## 4\.2 ', 'json')
    if len(ex) != 3:
        sys.exit(f"Mục 4.2 phải có đúng 3 ví dụ json, thấy {len(ex)}")
    names = ["result.e2e_pass.json", "result.e2e_fail.json", "result.ai_eval.json"]
    pathlib.Path("examples").mkdir(exist_ok=True)
    for n, b in zip(names, ex):
        obj = json.loads(b)
        pathlib.Path("examples", n).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("OK: schemas/result.json + 3 examples")

if __name__ == "__main__":
    main()
