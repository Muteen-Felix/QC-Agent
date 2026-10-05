"""Hồ sơ đăng nhập cho test Ground-Truth: `.qc-agent/ground-truth/auth.yaml` (QA sở hữu, KHÔNG chứa bí mật).

    version: 1
    login:
      method: POST
      path: /api/auth/login
      json:                                  # giá trị là hằng, hoặc {env: QC_TEST_...} lấy từ biến môi trường lúc chạy
        username: {env: QC_TEST_USERNAME}
        password: {env: QC_TEST_PASSWORD}
      token_path: $.access_token             # chỗ lấy token trong response (tập con JSONPath: $, .key, [n])
    header: {name: Authorization, scheme: Bearer}
    scope: session                           # session (mặc định): đăng nhập một lần; case: mỗi test case một phiên

Runtime (`gt-conftest.py.tmpl`) tự đăng nhập và gắn header vào mọi request, trừ chính endpoint đăng nhập. Header có giá trị rỗng nghĩa là KHÔNG gửi
(test "thiếu token"); header tường minh khác (vd `Bearer invalid`) được giữ nguyên. Khoá chỉ đi theo biến môi trường `QC_TEST_*`: file này được commit và
chỉ chấp nhận tên biến đó, nên không thể dùng để đưa biến môi trường khác (vd khoá LLM) vào request tới SUT.

Module này dùng cho `gt validate` và cho prompt. Runtime có bản kiểm tra riêng (conftest chỉ được dùng httpx, PyYAML, pytest); `tests/test_gt_auth.py`
so hai bản để chúng không lệch nhau.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

PROFILE_PATH = ".qc-agent/ground-truth/auth.yaml"
ENV_NAME = re.compile(r"QC_TEST_[A-Z0-9_]{1,40}")
_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,198}")
_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,31}")
_JSONPATH = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[[0-9]+\]){1,8}")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_SCALAR = (str, int, float, bool, type(None))
_TOP = {"version", "login", "header", "scope"}
_LOGIN = {"method", "path", "json", "token_path"}


def validate(data) -> list[str]:
    """Danh sách lỗi (rỗng = hợp lệ). Thông điệp chỉ nêu tên trường, không trích giá trị."""
    if not isinstance(data, dict):
        return ["auth.yaml phải là một object"]
    problems = [f"khoá lạ ở gốc: {key}" for key in sorted(map(str, data.keys() - _TOP))]
    if data.get("version") != 1:
        problems.append("version phải là 1")
    login = data.get("login")
    if not isinstance(login, dict):
        problems.append("thiếu login (object)")
    else:
        problems += [f"login: khoá lạ {key}" for key in sorted(map(str, login.keys() - _LOGIN))]
        if login.get("method", "POST") != "POST":
            problems.append("login.method chỉ hỗ trợ POST")
        path = login.get("path")
        if not (isinstance(path, str) and _PATH.fullmatch(path) and ".." not in path and not path.startswith("//")):
            problems.append("login.path phải là đường dẫn tương đối bắt đầu bằng / (không URL tuyệt đối, không ..)")
        token_path = login.get("token_path")
        if not (isinstance(token_path, str) and _JSONPATH.fullmatch(token_path)):
            problems.append("login.token_path phải dạng JSONPath đơn giản, vd $.access_token")
        body = login.get("json", {})
        if not isinstance(body, dict):
            problems.append("login.json phải là object")
        else:
            for key, value in body.items():
                if not (isinstance(key, str) and _KEY.fullmatch(key)):
                    problems.append("login.json: tên trường không hợp lệ")
                elif isinstance(value, dict):
                    name = value.get("env")
                    if set(value) != {"env"} or not (isinstance(name, str) and ENV_NAME.fullmatch(name)):
                        problems.append(f"login.json.{key}: chỉ nhận {{env: QC_TEST_...}} (tên biến phải bắt đầu bằng QC_TEST_)")
                elif not isinstance(value, _SCALAR):
                    problems.append(f"login.json.{key}: chỉ nhận hằng hoặc {{env: ...}}")
    header = data.get("header", {})
    if not isinstance(header, dict) or set(header) - {"name", "scheme"}:
        problems.append("header phải là object chỉ có name và scheme")
    else:
        if not (isinstance(header.get("name", "Authorization"), str) and _HEADER.fullmatch(header.get("name", "Authorization"))):
            problems.append("header.name không hợp lệ")
        scheme = header.get("scheme", "Bearer")
        if not (scheme == "" or (isinstance(scheme, str) and _SCHEME.fullmatch(scheme))):
            problems.append("header.scheme không hợp lệ (để chuỗi rỗng nếu gửi token trần)")
    if data.get("scope", "session") not in ("session", "case"):
        problems.append("scope phải là session hoặc case")
    return problems


CI_ENV = frozenset({"QC_TEST_USERNAME", "QC_TEST_PASSWORD"})   # hai biến mà .github/workflows/qc-gate.reusable.yml truyền vào container


def env_names(data: dict) -> set[str]:
    body = (data.get("login") or {}).get("json") or {}
    return {value["env"] for value in body.values() if isinstance(value, dict)}


def header_name(data: dict) -> str:
    return (data.get("header") or {}).get("name", "Authorization")


def load(sut_root: Path) -> dict | None:
    """Hồ sơ đã kiểm của repo SUT; None nếu chưa có file. File có nhưng sai thì ném ValueError (thông điệp không chứa giá trị)."""
    path = Path(sut_root) / PROFILE_PATH
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError(f"{PROFILE_PATH} không đọc được") from None
    problems = validate(data)
    if problems:
        raise ValueError(f"{PROFILE_PATH} sai: " + "; ".join(problems[:4]))
    return data


def prompt_block(data: dict | None) -> str | None:
    """Khối `<auth>` thêm vào prompt khi repo có hồ sơ. LLM không bao giờ thấy token hay khoá: chỉ biết runtime đã tự xác thực."""
    if data is None:
        return None
    name, login = header_name(data), data["login"]
    return ("<auth>\n"
            f"The test runtime logs in with a team-owned test account and sends the `{name}` header on every request by itself. "
            "Never skip or waive an endpoint because it needs a token or login, and never write credentials or tokens yourself.\n"
            f"- To test a MISSING credential, set the `{name}` header to the empty string \"\" (the runtime then sends no such header).\n"
            f"- To test an INVALID credential, set the `{name}` header to \"Bearer invalid\".\n"
            f"- The login endpoint itself ({login.get('method', 'POST')} {login['path']}) never receives the automatic header: "
            "test it with its own request body; a wrong password is a valid negative case.\n"
            "</auth>")
