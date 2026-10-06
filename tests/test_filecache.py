"""llm/filecache.py (S4-02): ghi nguyên tử, đọc chịu lỗi, khoá không thoát khỏi thư mục, prune."""
import os
import time

from qc_agent.llm import filecache

KEY = filecache.make_key("a", "b")


def test_key_is_unambiguous_and_stable():
    assert filecache.make_key("ab", "c") != filecache.make_key("a", "bc")
    assert filecache.make_key("a", "b") == KEY and len(KEY) == 64
    assert filecache.digest({"b": 1, "a": 2}) == filecache.digest({"a": 2, "b": 1})


def test_write_then_read_round_trip_leaves_no_temp_file(tmp_path):
    assert filecache.write(tmp_path / "d", KEY, {"x": "tiếng Việt"})
    assert filecache.read(tmp_path / "d", KEY) == {"x": "tiếng Việt"}
    assert [p.name for p in (tmp_path / "d").iterdir()] == [f"{KEY}.json"]


def test_read_is_a_miss_for_missing_corrupt_oversized_or_non_dict(tmp_path, monkeypatch):
    assert filecache.read(tmp_path, KEY) is None
    (tmp_path / f"{KEY}.json").write_text("{khong phai json", encoding="utf-8")
    assert filecache.read(tmp_path, KEY) is None
    (tmp_path / f"{KEY}.json").write_text("[1]", encoding="utf-8")
    assert filecache.read(tmp_path, KEY) is None
    monkeypatch.setattr(filecache, "MAX_ENTRY_BYTES", 5)
    (tmp_path / f"{KEY}.json").write_text('{"a": 1}', encoding="utf-8")
    assert filecache.read(tmp_path, KEY) is None


def test_a_key_that_is_not_a_sha256_never_touches_the_filesystem(tmp_path):
    for bad in ("../escape", "a/b", KEY[:-1], KEY.upper(), ""):
        assert filecache.read(tmp_path, bad) is None and filecache.write(tmp_path, bad, {}) is False
    assert list(tmp_path.iterdir()) == []


def test_write_failure_returns_false_and_cleans_up(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert filecache.write(blocker / "sub", KEY, {}) is False
    assert filecache.write(tmp_path / "d", KEY, {"bad": object()}) is False
    assert not any(p.name.endswith(".tmp") for p in (tmp_path / "d").iterdir())


def test_prune_keeps_the_newest_entries_and_a_read_refreshes_an_entry(tmp_path):
    keys = [filecache.make_key(str(i)) for i in range(5)]
    for age, key in enumerate(keys):
        filecache.write(tmp_path, key, {"i": age})
        os.utime(tmp_path / f"{key}.json", (time.time() - 100 + age, time.time() - 100 + age))   # keys[4] mới nhất
    filecache.read(tmp_path, keys[0])                      # đọc keys[0]: thành mới nhất
    filecache.prune(tmp_path, keep=2)
    assert {p.stem for p in tmp_path.glob("*.json")} == {keys[0], keys[4]}


def test_prune_ignores_foreign_files_and_a_missing_directory(tmp_path):
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    filecache.prune(tmp_path, keep=0)
    filecache.prune(tmp_path / "nope", keep=0)
    assert (tmp_path / "keep.txt").exists()
