import json, pathlib
from toyapp.summarizer import summarize

G = json.loads(pathlib.Path("tests/eval/golden.json").read_text(encoding="utf-8-sig"))

def test_bug_off_all_shorter():
    assert all(0 < len(summarize(g["body"], False)) < len(g["body"]) for g in G)

def test_bug_on_only_g3_longer():
    assert [g["id"] for g in G if len(summarize(g["body"], True)) >= len(g["body"])] == ["g3"]

def test_deterministic():
    assert all(summarize(g["body"]) == summarize(g["body"]) for g in G)
