import pytest

from core.verdict import gate_verdict


def R(status="pass", value="pass", gating=True, src="deterministic_assert", rat=None):
    return {"status": status, "verdict": {"value": value, "gating": gating, "verdict_source": src, "rationale": rat}}


G = {"lane": "gate"}
D = {"lane": "discovery"}
SKIP = R("skipped", "non_gating", False, "heuristic", "thiếu key")
ERR = R("error", "fail", False, "heuristic", "crash")

CASES = [
    ("all_pass",              {"a": G, "b": G},           {"a": R(), "b": R()},                    "PASS", 0),
    ("one_fail",              {"a": G, "b": G},           {"a": R(), "b": R("fail", "fail")},      "FAIL", 1),
    ("error_in_gate",         {"a": G, "b": G},           {"a": R(), "b": ERR},                    "FAIL", 1),
    ("error_in_discovery",    {"a": G, "m": D},           {"a": R(), "m": ERR},                    "PASS", 0),
    ("skipped_gate_yellow",   {"a": G, "b": G},           {"a": R(), "b": SKIP},                   "YELLOW", 0),
    ("discovery_finding_ok",  {"a": G, "m": D},           {"a": R(), "m": R("pass", "non_gating", False, "heuristic")}, "PASS", 0),
    ("empty_and_is_fail",     {"a": G},                   {"a": SKIP},                             "FAIL", 1),
    ("missing_gate_result",   {"a": G, "b": G},           {"a": R()},                              "FAIL", 1),
    ("fail_beats_skipped",    {"a": G, "b": G, "c": G},   {"a": R("fail", "fail"), "b": SKIP, "c": R()}, "FAIL", 1),
    ("llm_never_in_verdict",  {"a": G},                   {"a": R()},                              "PASS", 0),
]


@pytest.mark.parametrize("name,specs,results,value,code", CASES, ids=[c[0] for c in CASES])
def test_gate_verdict(name, specs, results, value, code):
    g = gate_verdict(results, specs)
    assert (g.value, g.exit_code) == (value, code)


def test_banner_lists_skipped_and_error():
    g = gate_verdict({"a": R(), "b": SKIP, "c": ERR}, {"a": G, "b": G, "c": G})
    assert {t for t, _ in g.banner} == {"b", "c"}
