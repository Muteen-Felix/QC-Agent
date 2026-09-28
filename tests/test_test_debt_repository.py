"""P2-4: sổ nợ test trên PostgreSQL thật — migration 0005 (lên/xuống) và `repository.apply_debt` (mở idempotent, diff-scan không đóng,
full-scan đóng đúng phần vắng mặt). Cần QC_TEST_DATABASE_URL như test_jobs_db.py; thiếu thì skip."""
import threading
import uuid

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from qc_agent.jobs import migrate, repository as repo
from qc_agent.jobs.db import make_engine, session_scope
from qc_agent.jobs.models import DebtEntry
from tests.dbfix import TABLES, db_url, engine, make_db, project, requires_pg  # noqa: F401  (fixtures)

pytestmark = requires_pg

A = ("api_endpoint", "GET /ping")
B = ("api_endpoint", "POST /items")
C = ("ui_route", "/settings")
D = ("api_contract", "GET /c")


def new_job(engine, slug="noteboard") -> uuid.UUID:
    with session_scope(engine) as s:
        return repo.create_job(s, slug, mode="pr", source="ci").id


def apply(engine, job, findings, *, full=False, slug="noteboard", **kw) -> repo.DebtDelta:
    with session_scope(engine) as s:
        return repo.apply_debt(s, slug, job_id=job, findings=findings, full_scan=full, **kw)


def entries(engine, slug="noteboard", *, open_only=False) -> dict:
    """{(kind, surface): [dòng, ...]} để test soi cả lịch sử đã đóng."""
    with session_scope(engine) as s:
        out: dict = {}
        for row in repo.list_debt(s, slug, open_only=open_only):
            out.setdefault((row.kind, row.surface), []).append(row)
        return out


def open_keys(engine, slug="noteboard") -> set:
    return set(entries(engine, slug, open_only=True))


# ---- migration ----

def test_migration_up_down_up_creates_and_removes_only_test_debt(make_db):
    url = make_db()
    migrate.upgrade(url, "0004")
    eng = make_engine(url)
    assert "test_debt" not in inspect(eng).get_table_names()
    eng.dispose()

    migrate.upgrade(url)  # 0005
    eng = make_engine(url)
    insp = inspect(eng)
    assert {"test_debt", "jobs", "projects"} <= set(insp.get_table_names())
    columns = {c["name"]: c for c in insp.get_columns("test_debt")}
    assert set(columns) == {"id", "project_id", "kind", "surface", "opened_at", "closed_at", "closed_reason", "pr_url",
                            "opened_job_id", "last_seen_job_id", "last_seen_at"}
    assert {n for n, c in columns.items() if not c["nullable"]} == {"id", "project_id", "kind", "surface", "opened_at",
                                                                     "opened_job_id", "last_seen_job_id", "last_seen_at"}
    unique_open = next(i for i in insp.get_indexes("test_debt") if i["name"] == "uq_test_debt_open")
    assert unique_open["unique"] and unique_open["column_names"] == ["project_id", "kind", "surface"]
    assert "closed_at IS NULL" in unique_open["dialect_options"]["postgresql_where"]  # PARTIAL: chỉ ràng buộc dòng đang mở
    assert not [c for c in insp.get_check_constraints("test_debt") if "kind" in c["sqltext"]]  # cố ý không CHECK trên kind
    eng.dispose()

    migrate.downgrade(url, "0004")
    eng = make_engine(url)
    assert "test_debt" not in inspect(eng).get_table_names() and "jobs" in inspect(eng).get_table_names()
    eng.dispose()

    migrate.upgrade(url)  # lên lại được
    eng = make_engine(url)
    assert "test_debt" in inspect(eng).get_table_names()
    eng.dispose()
    migrate.downgrade(url)  # xuống tận base cũng sạch
    eng = make_engine(url)
    assert not (set(TABLES) & set(inspect(eng).get_table_names()))
    eng.dispose()


def test_head_revision_is_0005():
    assert migrate.head_revision() == "0005"


def test_table_constraints_kind_is_free_form_and_only_one_open_row_per_surface(engine, project):
    job = new_job(engine)
    pid = _project_id(engine)

    def insert(kind="api_endpoint", surface="GET /x", closed_at=None, reason=None):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO test_debt (id, project_id, kind, surface, opened_job_id, last_seen_job_id, closed_at, closed_reason) "
                "VALUES (:id, :p, :k, :s, :j, :j, :c, :r)"), {"id": uuid.uuid4(), "p": pid, "k": kind, "s": surface, "j": job, "c": closed_at, "r": reason})

    insert(kind="tc_missing_phase3")  # kind tự do: không CHECK
    insert()
    with pytest.raises(IntegrityError):  # hai dòng ĐANG MỞ cùng (project, kind, surface)
        insert()
    insert(closed_at="2026-01-01T00:00:00Z", reason="covered")  # dòng đã đóng thì được trùng (lịch sử)
    insert(closed_at="2026-01-02T00:00:00Z", reason="ignored")
    with pytest.raises(IntegrityError):  # lý do đóng ngoài enum
        insert(surface="GET /y", closed_at="2026-01-01T00:00:00Z", reason="whatever")
    with pytest.raises(IntegrityError):  # đóng mà không có lý do
        insert(surface="GET /z", closed_at="2026-01-01T00:00:00Z")
    with pytest.raises(IntegrityError):  # có lý do mà chưa đóng
        insert(surface="GET /w", reason="covered")


def _project_id(engine) -> int:
    with session_scope(engine) as s:
        return repo.get_project(s, "noteboard").id


# ---- apply_debt: mở, idempotent ----

def test_apply_twice_with_same_findings_is_idempotent(engine, project):
    job = new_job(engine)
    first = apply(engine, job, [A, B])
    again = apply(engine, job, [A, B])
    assert first.opened == (A, B) and first.refreshed == () and first.closed == ()
    assert again.opened == () and again.refreshed == (A, B)
    rows = entries(engine)
    assert set(rows) == {A, B} and all(len(v) == 1 for v in rows.values())  # không có bản ghi trùng


def test_reapply_from_a_later_job_only_refreshes_last_seen(engine, project):
    first_job, second_job = new_job(engine), new_job(engine)
    apply(engine, first_job, [A], pr_url="https://github.com/o/r/pull/1")
    before = entries(engine)[A][0]
    apply(engine, second_job, [A], pr_url="https://github.com/o/r/pull/2")
    after = entries(engine)[A][0]
    assert after.id == before.id and after.opened_at == before.opened_at
    assert after.opened_job_id == first_job and after.last_seen_job_id == second_job  # nguồn gốc giữ nguyên, last_seen dịch chuyển
    assert after.last_seen_at > before.last_seen_at
    assert after.pr_url == "https://github.com/o/r/pull/1"  # PR làm phát sinh nợ không bị ghi đè
    assert after.closed_at is None and after.closed_reason is None


def test_concurrent_applies_of_the_same_finding_leave_one_open_row(engine, project):
    jobs = [new_job(engine) for _ in range(4)]
    barrier, errors = threading.Barrier(len(jobs)), []

    def worker(job):
        try:
            barrier.wait()
            apply(engine, job, [A, B])
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(j,)) for j in jobs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    rows = entries(engine)
    assert set(rows) == {A, B} and all(len(v) == 1 for v in rows.values())


def test_more_findings_than_one_chunk(engine, project):
    many = [("api_endpoint", f"GET /r{i}") for i in range(repo._DEBT_CHUNK * 2 + 7)]
    job = new_job(engine)
    assert len(apply(engine, job, many).opened) == len(many)
    assert len(open_keys(engine)) == len(many)
    delta = apply(engine, job, [many[0]], full=True)  # full-scan chỉ còn thấy 1: đóng phần còn lại qua nhiều chunk
    assert len(delta.closed) == len(many) - 1 and open_keys(engine) == {many[0]}


# ---- apply_debt: diff-scan chỉ mở ----

def test_diff_scan_never_closes_debts_outside_the_diff(engine, project):
    old_job, pr_job = new_job(engine), new_job(engine)
    apply(engine, old_job, [A, B])
    delta = apply(engine, pr_job, [C], full=False)  # PR này chỉ chạm C; A, B vắng mặt vì nằm ngoài diff
    assert delta.opened == (C,) and delta.closed == ()
    assert open_keys(engine) == {A, B, C}
    assert all(row.closed_at is None for rows in entries(engine).values() for row in rows)

    assert apply(engine, new_job(engine), [], full=False).closed == ()  # diff-scan không thấy gì: vẫn không đóng
    assert open_keys(engine) == {A, B, C}


# ---- apply_debt: full-scan đóng phần vắng mặt ----

def test_full_scan_closes_exactly_the_debts_it_no_longer_sees(engine, project):
    apply(engine, new_job(engine), [A, B, C, D])
    scan = new_job(engine)
    delta = apply(engine, scan, [B, D], full=True)  # A, C đã có test; B, D vẫn còn nợ
    assert delta.closed == ((*A, "covered"), (*C, "covered"))
    assert delta.refreshed == (D, B)
    assert open_keys(engine) == {B, D}
    rows = entries(engine)
    for key in (A, C):
        (row,) = rows[key]
        assert row.closed_reason == "covered" and row.closed_at is not None and row.last_seen_job_id != scan
    assert all(r.last_seen_job_id == scan for k in (B, D) for r in rows[k])


def test_full_scan_reason_ignored_beats_surface_gone_beats_covered(engine, project):
    apply(engine, new_job(engine), [A, B, C])
    delta = apply(engine, new_job(engine), [], full=True, ignored=[A], gone=[A, B])
    assert set(delta.closed) == {(*A, "ignored"), (*B, "surface_gone"), (*C, "covered")}
    rows = entries(engine)
    assert {k: v[0].closed_reason for k, v in rows.items()} == {A: "ignored", B: "surface_gone", C: "covered"}


def test_full_scan_only_closes_kinds_in_scope_and_only_its_own_project(engine, project):
    with session_scope(engine) as s:
        repo.sync_project(s, "other")
    phase3 = ("tc_missing", "TC-042")
    apply(engine, new_job(engine), [A, phase3])                                   # phase3: loại khác dùng chung bảng
    apply(engine, new_job(engine, "other"), [A], slug="other")                     # cùng surface ở project khác
    apply(engine, new_job(engine), [], full=True)                                  # full-scan rỗng của noteboard
    assert open_keys(engine) == {phase3}                                           # A đóng; loại khác không bị đóng oan
    assert open_keys(engine, "other") == {A}                                       # project khác không bị động tới
    apply(engine, new_job(engine), [], full=True, kinds=["tc_missing"])            # phạm vi tường minh
    assert open_keys(engine) == set()


def test_reappearing_debt_after_closing_is_a_new_row_and_history_is_kept(engine, project):
    apply(engine, new_job(engine), [A])
    apply(engine, new_job(engine), [], full=True)
    reopened = apply(engine, new_job(engine), [A])
    assert reopened.opened == (A,)
    rows = entries(engine)[A]
    assert len(rows) == 2 and sorted(r.closed_at is None for r in rows) == [False, True]
    assert {r.closed_reason for r in rows} == {None, "covered"}
    assert len(entries(engine, open_only=True)[A]) == 1


def test_empty_full_scan_is_the_only_way_to_clear_and_diff_scan_with_same_input_is_not(engine, project):
    apply(engine, new_job(engine), [A])
    apply(engine, new_job(engine), [], full=False)
    assert open_keys(engine) == {A}
    apply(engine, new_job(engine), [], full=True)
    assert open_keys(engine) == set()


# ---- kiểm tra đầu vào ----

def test_invalid_input_is_rejected_before_touching_the_table(engine, project):
    job = new_job(engine)
    for bad in ([("api_endpoint",)], ["GET /x"], [("", "GET /x")], [("api_endpoint", "")], [("k" * 33, "s")],
                [("api_endpoint", "s" * 1025)], [(None, "s")], [("api_endpoint", 5)]):
        with pytest.raises(ValueError):
            apply(engine, job, bad)
    with pytest.raises(ValueError):
        apply(engine, job, [A], ignored=["x"])
    assert entries(engine) == {}


def test_unknown_project_and_unknown_job_are_errors(engine, project):
    with pytest.raises(repo.ProjectNotFound):
        apply(engine, new_job(engine), [A], slug="nope")
    with pytest.raises(IntegrityError):  # job phải tồn tại (FK): không ghi nợ mồ côi
        apply(engine, uuid.uuid4(), [A])
    assert entries(engine) == {}


def test_failed_apply_rolls_back_as_a_whole(engine, project):
    apply(engine, new_job(engine), [A])
    with pytest.raises(IntegrityError):
        apply(engine, uuid.uuid4(), [B], full=True)  # mở B thất bại (job không có) => không được đóng A giữa chừng
    assert open_keys(engine) == {A}


# ---- list_debt ----

def test_list_debt_filters(engine, project):
    apply(engine, new_job(engine), [A, B, C])
    apply(engine, new_job(engine), [B], full=True)
    with session_scope(engine) as s:
        assert [(r.kind, r.surface) for r in repo.list_debt(s, "noteboard")] == [B]
        assert {(r.kind, r.surface) for r in repo.list_debt(s, "noteboard", open_only=False)} == {A, B, C}
        assert [(r.kind, r.surface) for r in repo.list_debt(s, "noteboard", open_only=False, kind="ui_route")] == [C]
        assert isinstance(s.scalar(select(DebtEntry.id).limit(1)), uuid.UUID)
