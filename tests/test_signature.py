import hashlib
import json

from core import signature


def _result(name="k6", version="1.0", adapter_version="adapter-1"):
    return {
        "worker": {
            "name": name,
            "version": version,
            "adapter_version": adapter_version,
        }
    }


def test_run_signature_is_stable_for_same_gate_inputs():
    specs = {
        "t-2": {"lane": "gate", "determinism": {"seed": 22}},
        "t-1": {"lane": "gate", "determinism": {"seed": 11}},
    }
    results = {"t-2": _result("zeta"), "t-1": _result("alpha")}

    first = signature.run_signature("plan-a", "sut-a", results, specs)
    reordered = signature.run_signature(
        "plan-a", "sut-a", dict(reversed(list(results.items()))),
        dict(reversed(list(specs.items()))),
    )

    assert first == reordered
    assert len(first) == 64


def test_run_signature_changes_when_gate_worker_version_or_adapter_changes():
    specs = {"t-1": {"lane": "gate", "determinism": {"seed": 11}}}
    baseline = {"t-1": _result()}
    baseline_signature = signature.run_signature("plan-a", "sut-a", baseline, specs)

    changed_adapter = {"t-1": _result(adapter_version="adapter-2")}
    changed_worker = {"t-1": _result(version="2.0")}

    assert signature.run_signature("plan-a", "sut-a", changed_adapter, specs) != baseline_signature
    assert signature.run_signature("plan-a", "sut-a", changed_worker, specs) != baseline_signature


def test_plan_id_changes_when_plan_content_changes():
    assert signature.plan_id("name: before\n") != signature.plan_id("name: after\n")


def test_plan_id_normalizes_crlf_to_lf():
    assert signature.plan_id("name: demo\r\ntasks: []\r\n") == signature.plan_id("name: demo\ntasks: []\n")


def test_sut_id_tracks_source_content_but_ignores_newlines_and_python_cache(tmp_path):
    source = tmp_path / "sut" / "app.py"
    source.parent.mkdir()
    source.write_bytes(b"VALUE = 1\r\n")
    cache = source.parent / "__pycache__" / "app.cpython-311.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"cache version one")
    cfg = {"files": ["sut"], "attrs": {"model": "stub-rule-v1"}}

    crlf_identity = signature.sut_identity(cfg, tmp_path)
    source.write_bytes(b"VALUE = 1\n")
    cache.write_bytes(b"cache version two")
    lf_identity = signature.sut_identity(cfg, tmp_path)

    assert crlf_identity["files_sha256"] == lf_identity["files_sha256"]
    assert signature.sut_id(crlf_identity) == signature.sut_id(lf_identity)
    expected = "sut-" + hashlib.sha256(
        json.dumps(crlf_identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    assert signature.sut_id(crlf_identity) == expected

    cache_metadata = cache.parent / "metadata.txt"
    cache_metadata.write_text("included cache metadata", encoding="utf-8")
    assert signature.sut_id(signature.sut_identity(cfg, tmp_path)) != signature.sut_id(lf_identity)
    cache_metadata.unlink()
    outside_cache = source.parent / "fixture.pyc"
    outside_cache.write_bytes(b"not under __pycache__")
    assert signature.sut_id(signature.sut_identity(cfg, tmp_path)) != signature.sut_id(lf_identity)
    outside_cache.unlink()

    source.write_bytes(b"VALUE = 2\n")
    changed_identity = signature.sut_identity(cfg, tmp_path)
    assert signature.sut_id(lf_identity) != signature.sut_id(changed_identity)


def test_sut_identity_probe_is_optional_nonfatal_and_persisted(tmp_path, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"bugs": [], "latency_ms": 0}'

    monkeypatch.setattr(signature, "urlopen", lambda _url, timeout: FakeResponse())
    assert signature._probe_config("http://example.test/__qc/config") == {"bugs": [], "latency_ms": 0}
    assert signature._probe_config("http://[malformed") == {"error": "ValueError"}

    def offline(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(signature, "urlopen", offline)
    cfg = {"files": [], "attrs": {}, "probe_url": "http://example.test/__qc/config"}
    identity = signature.sut_identity(cfg, tmp_path)
    assert identity["probe"]["error"] == "OSError"

    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(signature.subprocess, "run", missing_git)
    assert signature.sut_identity({"files": [], "attrs": {}}, tmp_path)["code_commit"] == "no-git"

    written = signature.write_sut_identity(identity, tmp_path / "runs" / "r-0001")
    assert written == tmp_path / "runs" / "r-0001" / "sut_identity.json"
    assert json.loads(written.read_text(encoding="utf-8")) == identity


def test_discovery_results_do_not_change_signature_but_skipped_gate_worker_does():
    specs = {
        "gate-1": {"lane": "gate", "determinism": {"seed": 1}},
        "discovery-1": {"lane": "discovery", "determinism": {"seed": 2}},
    }
    gate_result = {"gate-1": _result()}
    initial = signature.run_signature("plan-a", "sut-a", gate_result, specs)
    with_discovery = {
        **gate_result,
        "discovery-1": _result("new-discovery-worker", "9.0", "discovery-adapter"),
    }
    skipped_gate = {"gate-1": _result("unknown", None, "core")}

    assert signature.run_signature("plan-a", "sut-a", with_discovery, specs) == initial
    assert signature.run_signature("plan-a", "sut-a", skipped_gate, specs) != initial
