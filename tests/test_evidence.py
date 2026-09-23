import hashlib

import pytest

from qc_agent.core import evidence


def test_sha256_matches_hashlib_on_raw_bytes(tmp_path):
    p = tmp_path / "x.bin"
    data = b"a\r\nb\n\x00\xff" * 1000  # CRLF + byte không phải UTF-8: phải băm byte thô
    p.write_bytes(data)
    assert evidence.sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_collect_uri_is_relative_posix(tmp_path):
    f = tmp_path / "runs" / "r-0001" / "t-001" / "x.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    ev = evidence.collect([("raw_output", f)], base=tmp_path)
    assert ev == [{"kind": "raw_output", "uri": "runs/r-0001/t-001/x.json",
                   "sha256": hashlib.sha256(b"{}").hexdigest()}]
    assert "\\" not in ev[0]["uri"]


def test_collect_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        evidence.collect([("stdout", tmp_path / "nope.log")], base=tmp_path)


def test_missing_kinds():
    have = [{"kind": "raw_output", "uri": "a", "sha256": "0" * 64}]
    assert evidence.missing_kinds(have, ["raw_output", "stdout", "trace"]) == ["stdout", "trace"]
    assert evidence.missing_kinds(have, ["raw_output"]) == []
    assert evidence.missing_kinds([], []) == []
