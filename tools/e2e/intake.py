"""Mẫu intake cho SUT dùng ở S4-06 và bộ kiểm "còn thiếu gì". Chỉ đọc file YAML cục bộ.

`null` nghĩa là CHƯA CÓ thông tin: mỗi `null` bắt buộc thành một dòng trong danh sách việc người dùng phải trả lời (`gaps`), kèm bước nào bị chặn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TEMPLATE = """\
# Intake S4-06: điền MỌI giá trị `null`. Giá trị nào chưa có thì để null: công cụ sẽ nêu rõ bước nào bị chặn.
# Không đặt bí mật vào file này (token, mật khẩu, API key): chỉ tên secret và nơi cất.
sut:
  name: noteboard                     # tên hiển thị
  project_slug: noteboard             # slug trong configs/projects/ của qc-agent@main (policy lấy từ nhánh main khi chạy)
  local_path: tests/fixtures/sut/noteboard   # bản checkout cục bộ để preflight; công cụ chỉ ĐỌC và chỉ chạy lệnh trên BẢN SAO TẠM
  commit: null                        # SHA 40 ký tự của SUT được nghiệm thu (cố định để có thể lặp lại)
  dockerfile: Dockerfile
  context: "."
  port: 8000
  health_path: /                      # đường dẫn trả lời HTTP khi sẵn sàng (noteboard không có /health; workflow coi mọi mã HTTP là sẵn sàng)
  build_command: docker build -f Dockerfile -t qc-sut .
  test_command: null                  # vd. python -m pytest -q tests ; chạy trên bản sao tạm khi preflight --run-sut-tests
  openapi: docs/openapi.json          # đường dẫn trong repo SUT
  prd_glob: docs/prd/**
  expected_findings:                  # đối chiếu ở bước thu kết quả; severity ∈ critical|medium|low
    C: [{severity: critical, note: "BUGS.add(1) trong toyapp/app.py (BUG-1)"}]
    D: [{severity: low, rule_id: api_endpoint, path: toyapp/app.py}]
  db:
    needed: false                     # true => cần S4-09: sut_db_image (digest), sut_db_ready_cmd|sut_db_port, secret SUT_SECRET_ENV/SUT_DB_SECRET_ENV
    image: null
    ready_cmd: null
github:
  sandbox_repo: null                  # OWNER/REPO của repo sandbox
  plan: null                          # free | pro | team | enterprise (GitHub Free + repo private KHÔNG có branch protection => kịch bản B không chạy được)
  visibility: null                    # public | private
  qa_team: null                       # @org/team dùng trong CODEOWNERS (không phải ví dụ giữ chỗ)
  non_qa_account: null                # login KHÔNG thuộc team QA, có quyền write nhưng KHÔNG phải admin (bản clone riêng); dùng để thử push vào .qc-agent/
  dev_account: null                   # login tác giả PR của kịch bản C/D (phải có trong jira.user_map)
  qc_agent_repo: Muteen-Felix/QC-Agent   # nơi workflow tái sử dụng và policy@main nằm; phải đọc được từ repo sandbox (public, hoặc secret qc_read_token)
  qc_ref: null                        # SHA 40 ký tự của qc-agent để ghim workflow
  image: null                         # ghcr.io/...@sha256:<64 hex>
jira:
  base_url: null                      # https://<site>.atlassian.net (sandbox)
  project_key: null
  user_map: {}                        # {github_login: jira_accountId}; nằm trong configs/projects/<slug>.yaml của qc-agent@main
llm:
  egress_question3: pending           # confirmed | pending: xác nhận bằng văn bản được gửi PRD/diff (và mã nguồn nếu bật agent) qua LLM ngoài
  egress_evidence: null               # tham chiếu tới văn bản xác nhận (không dán nội dung)
  provider: anthropic
  budget_usd_max: null                # trần tổng cho cả S4-06 (số), cũng đặt làm spend limit của workspace API key
  enable_gt_agent: false              # true gửi MÃ NGUỒN SUT ra ngoài: chỉ bật khi câu hỏi #3 cho phép rõ
  judge_and_midscene_keys: false      # true = tạo cả OPENAI_API_KEY/GEMINI_API_KEY/MIDSCENE_*: thêm kênh LLM khác Anthropic (G-Eval, UI explore)
"""

_SHA40 = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TEAM = re.compile(r"^@[A-Za-z0-9_.-]{1,39}(?:/[A-Za-z0-9_.-]{1,100})?$")
_PLANS = ("free", "pro", "team", "enterprise")
KNOWN = {"sut", "github", "jira", "llm"}


@dataclass(frozen=True)
class Gap:
    key: str
    group: str            # sut | github | jira | llm | db
    blocks: str           # các bước bị chặn
    reason: str
    blocker: bool = True   # False = cảnh báo


def load(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("intake phải là một mapping YAML")
    unknown = set(data) - KNOWN
    if unknown:
        raise ValueError(f"khoá lạ ở gốc intake: {sorted(unknown)}")
    return data


def template_data() -> dict:
    return yaml.safe_load(TEMPLATE)


def _get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate(data: dict) -> list[Gap]:
    """Danh sách điều còn thiếu/sai. Rỗng nghĩa là intake đủ để bắt đầu; KHÔNG nghĩa là S4-06 đạt."""
    from qc_agent.scaffold.validate import PLACEHOLDER_OWNERS

    gaps: list[Gap] = []

    def need(key: str, group: str, blocks: str, reason: str) -> bool:
        if _get(data, key) in (None, "", []):
            gaps.append(Gap(key, group, blocks, reason))
            return False
        return True

    if need("sut.commit", "sut", "mọi kịch bản", "SHA SUT cố định để chạy lặp lại được") and not _SHA40.fullmatch(str(_get(data, "sut.commit"))):
        gaps.append(Gap("sut.commit", "sut", "mọi kịch bản", "phải là SHA 40 ký tự hex thường"))
    need("sut.test_command", "sut", "preflight --run-sut-tests", "lệnh test của SUT")
    if _get(data, "sut.db.needed"):
        for key in ("sut.db.image", "sut.db.ready_cmd"):
            need(key, "db", "mọi kịch bản (SUT cần DB)", "S4-09: image DB ghim digest + lệnh sẵn sàng")
        image = _get(data, "sut.db.image")
        if image and not _DIGEST.fullmatch(str(image)):
            gaps.append(Gap("sut.db.image", "db", "mọi kịch bản", "image DB phải ghim @sha256:<64 hex>"))

    repo = _get(data, "github.sandbox_repo")
    if need("github.sandbox_repo", "github", "mọi kịch bản", "OWNER/REPO của repo sandbox") and not _REPO.fullmatch(str(repo)):
        gaps.append(Gap("github.sandbox_repo", "github", "mọi kịch bản", "phải có dạng OWNER/REPO"))
    plan, visibility = _get(data, "github.plan"), _get(data, "github.visibility")
    need("github.plan", "github", "B", f"một trong {_PLANS}")
    need("github.visibility", "github", "B", "public | private")
    if plan is not None and str(plan).lower() not in _PLANS:
        gaps.append(Gap("github.plan", "github", "B", f"phải là một trong {_PLANS}"))
    if str(plan).lower() == "free" and str(visibility).lower() == "private":
        gaps.append(Gap("github.visibility", "github", "B", "GitHub Free + repo private KHÔNG có branch protection: kịch bản B không chạy được (đổi repo sang public hoặc gói trả phí)"))
    team = _get(data, "github.qa_team")
    if need("github.qa_team", "github", "A, B", "team QA trong CODEOWNERS"):
        if not _TEAM.fullmatch(str(team)) or str(team) in PLACEHOLDER_OWNERS:
            gaps.append(Gap("github.qa_team", "github", "A, B", "phải là team thật, không phải ví dụ giữ chỗ (@org/team, @my-org/qa-team...)"))
    need("github.non_qa_account", "github", "B", "tài khoản không thuộc team QA để thử push vào .qc-agent/")
    dev = _get(data, "github.dev_account")
    need("github.dev_account", "github", "C, D", "login tác giả PR của dev")
    if _get(data, "github.qc_ref") is None:
        gaps.append(Gap("github.qc_ref", "github", "mọi kịch bản", "SHA qc-agent để ghim workflow"))
    elif not _SHA40.fullmatch(str(_get(data, "github.qc_ref"))):
        gaps.append(Gap("github.qc_ref", "github", "mọi kịch bản", "phải là SHA 40 ký tự hex thường"))
    image = _get(data, "github.image")
    if image is None:
        gaps.append(Gap("github.image", "github", "mọi kịch bản", "digest image qc-agent đã publish (lấy từ Job Summary của image.yml)"))
    elif not _DIGEST.fullmatch(str(image)):
        gaps.append(Gap("github.image", "github", "mọi kịch bản", "phải ghim @sha256:<64 hex>"))
    if not _REPO.fullmatch(str(_get(data, "github.qc_agent_repo") or "")):
        gaps.append(Gap("github.qc_agent_repo", "github", "mọi kịch bản", "OWNER/REPO của qc-agent"))

    need("jira.base_url", "jira", "D", "URL Jira sandbox (https)")
    need("jira.project_key", "jira", "D", "project key của Jira sandbox")
    user_map = _get(data, "jira.user_map") or {}
    if dev and dev not in user_map:
        gaps.append(Gap("jira.user_map", "jira", "D", f"thiếu ánh xạ cho tác giả PR {dev!r}: ticket sẽ KHÔNG có người nhận. "
                                                       "user_map nằm trong configs/projects/<slug>.yaml của qc-agent@main nên cần một PR vào qc-agent (hành động ngoài, cần xác nhận)"))

    if _get(data, "llm.egress_question3") != "confirmed":
        gaps.append(Gap("llm.egress_question3", "llm", "mọi lượt LLM thật (A sinh GT, C, D, 10 lượt, đo token/recall)", "chưa có xác nhận bằng văn bản: các lượt LLM là PENDING; B và E vẫn chạy được"))
    elif not _get(data, "llm.egress_evidence"):
        gaps.append(Gap("llm.egress_evidence", "llm", "mọi lượt LLM thật", "ghi tham chiếu tới văn bản xác nhận", blocker=False))
    budget = _get(data, "llm.budget_usd_max")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0:
        gaps.append(Gap("llm.budget_usd_max", "llm", "mọi lượt LLM thật", "trần ngân sách (USD, số dương) cho cả bước"))
    if _get(data, "llm.enable_gt_agent"):
        gaps.append(Gap("llm.enable_gt_agent", "llm", "A", "GT agent gửi MÃ NGUỒN SUT ra ngoài và chưa từng chạy với API thật: cần xác nhận riêng cho nguồn mã", blocker=False))
    if _get(data, "llm.judge_and_midscene_keys"):
        gaps.append(Gap("llm.judge_and_midscene_keys", "llm", "mọi lần chạy gate", "thêm kênh LLM ngoài Anthropic (G-Eval 5 ca/lần, UI explore): không nằm trong ước tính Anthropic, không có giá trong llm/prices.py", blocker=False))
    return gaps


def write_template(path: Path, *, force: bool = False) -> bool:
    """Ghi mẫu; không ghi đè file đã có (trả False) trừ khi `force`."""
    path = Path(path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8", newline="\n")
    return True
