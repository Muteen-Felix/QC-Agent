"""Danh sách lệnh đầy đủ của S4-06, tách ba nhóm: offline / LLM thật / tác động GitHub-Jira. Chỉ in; không chạy lệnh nào."""
from __future__ import annotations

from tools.e2e import sandbox, scenarios
from tools.e2e.common import EXTERNAL, LLM, OFFLINE, Step

Q3 = "llm.egress_question3 = confirmed"
BUDGET = "ngân sách đã duyệt (python -m tools.e2e budget)"


def offline_steps() -> list[Step]:
    return [
        Step(OFFLINE, "Test công cụ S4-06 (fixture/fake, không mạng)", command="pytest tests/test_e2e_tools.py tests/test_e2e_evidence.py -q"),
        Step(OFFLINE, "Contract còn nguyên", command="python tools/freeze_contract.py --check"),
        Step(OFFLINE, "Kiểm intake: còn thiếu gì", command="python -m tools.e2e intake check runs/e2e/intake.yaml"),
        Step(OFFLINE, "Preflight (kèm test của SUT trên bản sao tạm)", command="python -m tools.e2e preflight --intake runs/e2e/intake.yaml --run-sut-tests --out runs/e2e/preflight.md"),
        Step(OFFLINE, "Dựng thử sandbox cục bộ (thư mục tạm, xoá sau)", command="python -m tools.e2e sandbox --intake runs/e2e/intake.yaml"),
        Step(OFFLINE, "Số lượt gọi, dữ liệu gửi đi, chi phí ước tính và trần đề xuất", command="python -m tools.e2e budget --iterations 10"),
        Step(OFFLINE, "Đo token OFFLINE (ước lượng ceil(byte/3), không phải count_tokens)", command="python tools/eval_cost.py --dataset noteboard --llm-tokens estimate"),
        Step(OFFLINE, "Recall với LLM GIẢ (chỉ kiểm đường chạy, KHÔNG chứng minh recall)", command="python tools/eval_selector.py --llm fake"),
        Step(OFFLINE, "A–E trên harness local (Docker + fake GitHub/Anthropic/Jira; KHÔNG phải GitHub thật)", command="python tools/run_reusable_locally.py --scenario full-chain",
             needs=("Docker chạy", "image build từ HEAD sạch (docs/e2e-runbook.md)")),
        Step(OFFLINE, "Đối chiếu bằng chứng đã thu với bảng kỳ vọng", command="python -m tools.e2e collect <evidence-dir> --intake runs/e2e/intake.yaml"),
        Step(OFFLINE, "Kiểm ngân sách từ llm_usage.json đã thu", command="python -m tools.e2e ledger <evidence-dir> --cap <USD>"),
        Step(OFFLINE, "So thời gian job gate có Select với baseline FULL SET", command="python -m tools.e2e timing --project noteboard --with-select <D/job.json ...> --baseline <baseline/*/job.json ...>"),
        Step(OFFLINE, "Tổng hợp 10 lượt", command="python -m tools.e2e stability summary <evidence-dir>/stability-results.json"),
    ]


def llm_steps() -> list[Step]:
    return [
        Step(LLM, "Đo token THẬT của golden set (count_tokens: gửi toàn bộ diff ra ngoài, có ghi egress)", needs=(Q3, BUDGET, "ANTHROPIC_API_KEY trong môi trường"),
             command="python tools/eval_cost.py --dataset noteboard --llm-tokens count --yes --out-json runs/e2e/cost-count.json"),
        Step(LLM, "Đo recall THẬT của Selector (median 3 lượt; tự dừng ở --max-usd)", needs=(Q3, BUDGET, "ANTHROPIC_API_KEY trong môi trường"),
             command="python tools/eval_selector.py --llm real --runs 3 --yes --max-usd <USD> --out-json runs/e2e/selector-real.json"),
        Step(LLM, "(Các lượt gate/GT thật chạy TRÊN GITHUB: kịch bản A, C, D và 10 lượt C+D, xem nhóm tác động GitHub bên dưới; chúng tốn tiền khi workflow gọi Anthropic)"),
    ]


def external_steps(intake: dict, iterations: int = 10) -> list[Step]:
    gh = intake.get("github", {})
    repo = gh.get("sandbox_repo") or "<OWNER/REPO>"
    project = (intake.get("sut") or {}).get("project_slug") or "noteboard"
    key = (intake.get("jira") or {}).get("project_key") or "<JIRA_KEY>"
    evidence = "runs/e2e/evidence"
    out = [s for s in sandbox.plan(intake) if s.kind == EXTERNAL]
    for sc in ("A", "B", "C", "D", "E"):
        out += [s for s in scenarios.steps(sc, repo=repo, project=project, key=key, evidence=evidence, dev=gh.get("dev_account") or "<dev>",
                                           non_qa=gh.get("non_qa_account") or "<non-qa>") if s.kind in (EXTERNAL, LLM)]
    out += [Step(LLM, f"{iterations} lượt C+D liên tiếp: sinh script rồi TỰ chạy (mỗi lượt hỏi xác nhận, kiểm ngân sách giữa các lượt)", needs=(Q3, BUDGET, "A–E đã xong"),
                 command=f"python -m tools.e2e stability script --intake runs/e2e/intake.yaml --out runs/e2e/stability.sh --iterations {iterations}\nbash runs/e2e/stability.sh"),
            Step(EXTERNAL, "Baseline thời gian: kích hoạt tay trên nhánh D (không workers => FULL SET, không Select), lặp ≥ 3 lần, lưu job.json mỗi lần", needs=("xác nhận của bạn",),
                 command=f"gh workflow run qc-gate.yml --repo {repo} --ref <nhánh-D>\ngh run view <RUN_ID> --repo {repo} --json jobs,conclusion,event,createdAt,updatedAt > {evidence}/baseline/<n>/job.json")]
    return out


def full_plan(intake: dict, iterations: int = 10) -> list[Step]:
    return offline_steps() + llm_steps() + external_steps(intake, iterations)
