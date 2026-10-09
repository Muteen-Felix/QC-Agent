"""Kịch bản A–E trên noteboard (S4-05), chạy bằng workflow THẬT trên Docker cục bộ: xem tests/full_chain.py (logic kịch bản, dùng chung với
`tools/run_reusable_locally.py --scenario full-chain`). Mỗi test in bảng kết quả khi đỏ; dòng CHƯA KIỂM CHỨNG không làm test đỏ nhưng được in bằng `-s` hoặc khi đỏ.

PHẠM VI KẾT LUẬN: workflow đúng cho project ĐÃ ĐĂNG KÝ (noteboard), stack Python, GitHub/Anthropic/Jira GIẢ. Không nói gì về repo dùng policy mặc định, GitHub Actions thật,
tạo PR thật (S4-06) hay chất lượng Selector. Thiếu Docker/image: SKIP = CHƯA KIỂM CHỨNG (QC_HARNESS_REQUIRE_DOCKER=1 biến skip thành lỗi)."""
import pytest

from tests import full_chain as fc
from tests import harness_kit as kit

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def image():
    return kit.verified_image().image


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    monkeypatch.delenv("QC_DATABASE_URL", raising=False)
    monkeypatch.delenv("QC_TEST_DATABASE_URL", raising=False)


def _assert_ok(rec: fc.Recorder, capsys=None):
    fc.safe_print("\n" + rec.table())
    assert not rec.failures(), rec.table()
    assert rec.rows, "kịch bản không ghi dòng nào"


def test_a_and_b_generate_ground_truth_then_validate_after_qa_approval(image):
    with kit.workspace_dir() as base_dir:
        a = fc.scenario_a(image, base_dir)
        _assert_ok(a)
        b = fc.scenario_b(image, base_dir / "sut")
        _assert_ok(b)


def test_c_critical_bug_blocks_the_pr(image):
    _assert_ok(fc.scenario_c(image))


def test_d_low_only_pr_passes_with_warnings_and_a_rerun_creates_nothing_new(image):
    rec = fc.scenario_d(image)
    _assert_ok(rec)
    assert [row[0] for row in rec.unverified_rows()] == ["ticket Jira (plan: 'đúng 1 ticket trong FakeJira')"], "dòng CHƯA KIỂM CHỨNG phải luôn hiện, không được biến mất âm thầm"


def test_e_workflow_dispatch_runs_only_the_requested_worker_without_llm(image):
    _assert_ok(fc.scenario_e(image))


def test_the_pr_flow_does_not_need_a_database(image, monkeypatch):
    """Không `qc_api_url` và không QC_DATABASE_URL: luồng PR của noteboard chạy tới Enforce (đã kiểm ở D/E; ở đây khoá điều kiện môi trường)."""
    import os
    assert "QC_DATABASE_URL" not in os.environ and "QC_TEST_DATABASE_URL" not in os.environ
