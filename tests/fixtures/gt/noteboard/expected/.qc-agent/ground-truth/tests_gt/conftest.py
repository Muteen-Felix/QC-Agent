# qc-agent:generated gt — runtime của test Ground-Truth. KHÔNG sửa tay: `qc-agent gt validate` render lại và so với file này.
"""Chạy các test case ĐÃ DUYỆT (status: approved) trong ../test-cases.yaml qua HTTP tới APP_BASE_URL.

Test điều khiển bằng dữ liệu: mã ở đây là mẫu cố định, chuỗi trong catalog (title, giá trị request, assertion) chỉ là DỮ LIỆU, không bao giờ được
biên dịch hay eval. Chỉ dùng httpx, PyYAML và pytest. Cấu hình sai (thiếu APP_BASE_URL, catalog hỏng, TC approved không có file story) thoát với
mã 4 để gate tính là `error` (hạ tầng), không phải `fail`. Một TC bị lệch kỳ vọng là `fail`.

Biến: bước có `capture` lưu giá trị theo tên; bước sau dùng dạng hai ngoặc nhọn quanh tên biến trong path_params, query và json. Chuỗi json chỉ gồm đúng một
biến thì giữ nguyên kiểu của giá trị đã capture; còn lại nội suy thành chuỗi.
Assertion: eq/ne so sánh kiểu JSON (true khác 1); ne, contains, len_* yêu cầu path tồn tại; exists đếm cả giá trị null; contains là chuỗi con hoặc phần tử scalar.
Xác thực: có `../auth.yaml` thì runtime tự đăng nhập (khoá lấy từ biến môi trường QC_TEST_*) và gắn token vào mọi request, trừ chính endpoint đăng nhập. Header có giá trị
rỗng nghĩa là KHÔNG gửi header đó (test thiếu token); header tường minh khác được giữ nguyên (test token sai). Sai cấu hình hoặc đăng nhập thất bại là `error`, không phải `fail`.
"""
import json
import os
import re
import urllib.parse
from pathlib import Path

import httpx
import pytest
import yaml

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parent / "test-cases.yaml"
AUTH_FILE = HERE.parent / "auth.yaml"
TIMEOUT_S = 10.0
EXIT_ERROR = 4
SHOW = 80
_VAR = re.compile(r"\{\{([a-z][a-z0-9_]{0,39})\}\}")
_PLACEHOLDER = re.compile(r"\{([^{}/]+)\}")
_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[([0-9]+)\]")
_MISSING = object()
_cache = {}
_AUTH_ENV = re.compile(r"QC_TEST_[A-Z0-9_]{1,40}")
_AUTH_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,198}")
_AUTH_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
_AUTH_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,31}")
_AUTH_JSONPATH = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[[0-9]+\]){1,8}")
_AUTH_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def story_slug(story_id):
    return re.sub(r"[^a-z0-9]+", "_", str(story_id).lower()).strip("_") or "story"


def _fatal(message):
    pytest.exit("qc-agent gt: " + message, returncode=EXIT_ERROR)


def _catalog():
    if "data" not in _cache:
        try:
            data = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            _fatal(f"không đọc được {CATALOG.name} ({type(error).__name__})")
        if not isinstance(data, dict) or not isinstance(data.get("stories"), list) or not isinstance(data.get("test_cases"), list):
            _fatal(f"{CATALOG.name} sai cấu trúc (cần stories và test_cases)")
        _cache["data"] = data
    return _cache["data"]


def _approved():
    """{story_id: [tc approved]}. TC approved không gắn được vào story nào, hoặc story không có file test, thì KHÔNG được lặng lẽ bỏ qua."""
    if "approved" not in _cache:
        data = _catalog()
        owner = {}
        for story in data["stories"]:
            for ac in (story.get("acs") or []) if isinstance(story, dict) else []:
                if isinstance(ac, dict) and "ac_id" in ac and isinstance(story.get("story_id"), str):
                    owner[ac["ac_id"]] = story["story_id"]
        grouped = {}
        for tc in data["test_cases"]:
            if not isinstance(tc, dict) or tc.get("status") != "approved":
                continue
            refs = tc.get("ac_refs")
            story_id = owner.get(refs[0]) if isinstance(refs, list) and refs else None
            if story_id is None or not (HERE / f"test_{story_slug(story_id)}.py").is_file():
                _fatal(f"TC approved {str(tc.get('tc_id'))[:60]} không thuộc story nào có file test_<story>.py; chạy `qc-agent gt regen`")
            grouped.setdefault(story_id, []).append(tc)
        _cache["approved"] = grouped
    return _cache["approved"]


def pytest_sessionstart(session):
    _approved()   # kiểm ở đây, không phải lúc collect: pytest.exit trong collect bị tính là lỗi collect (exit 2) thay vì mã 4


def pytest_generate_tests(metafunc):
    if "tc" not in metafunc.fixturenames:
        return
    cases = _approved().get(getattr(metafunc.module, "STORY_ID", None), [])
    if cases:
        metafunc.parametrize("tc", cases, ids=[str(tc.get("tc_id")) for tc in cases])
    else:
        metafunc.parametrize("tc", [None], ids=["no-approved-tc"])   # bị gỡ ở collection_modifyitems: không tính là test đã chạy


def pytest_collection_modifyitems(config, items):
    dropped = [item for item in items if getattr(getattr(item, "callspec", None), "params", {}).get("tc", 0) is None]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if item not in dropped]


def pytest_collection_finish(session):
    if not session.items:
        return
    base = os.environ.get("APP_BASE_URL", "").strip()
    if not base:
        _fatal("thiếu biến môi trường APP_BASE_URL")
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        _fatal("APP_BASE_URL phải là http(s)")


def _show(value):
    if value is _MISSING:
        return "<không có>"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= SHOW else text[: SHOW - 1] + "…"


def _text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _interpolate(text, variables, fail):
    def replace(match):
        if match.group(1) not in variables:
            fail(f"biến chưa có giá trị: {match.group(1)}")
        return _text(variables[match.group(1)])
    return _VAR.sub(replace, text)


def _fill(node, variables, fail):
    if isinstance(node, str):
        whole = _VAR.fullmatch(node)
        if whole:
            if whole.group(1) not in variables:
                fail(f"biến chưa có giá trị: {whole.group(1)}")
            return variables[whole.group(1)]
        return _interpolate(node, variables, fail)
    if isinstance(node, list):
        return [_fill(item, variables, fail) for item in node]
    if isinstance(node, dict):
        return {key: _fill(value, variables, fail) for key, value in node.items()}
    return node


def _lookup(root, path):
    if not isinstance(path, str) or not path.startswith("$"):
        return _MISSING
    node, position = root, 1
    while position < len(path):
        token = _TOKEN.match(path, position)
        if token is None:
            return _MISSING
        position = token.end()
        if token.group(1) is not None:
            if not isinstance(node, dict) or token.group(1) not in node:
                return _MISSING
            node = node[token.group(1)]
        else:
            index = int(token.group(2))
            if not isinstance(node, list) or index >= len(node):
                return _MISSING
            node = node[index]
    return node


def _same(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _holds(op, found, value):
    if op == "absent":
        return found is _MISSING
    if found is _MISSING:
        return False
    if op == "exists":
        return True
    if op == "eq":
        return _same(found, value)
    if op == "ne":
        return not _same(found, value)
    if op == "type":
        return value in _TYPES and _TYPES[value](found)
    if op in ("len_eq", "len_gte"):
        if not isinstance(found, (str, list)) or isinstance(value, bool) or not isinstance(value, int):
            return False
        return len(found) == value if op == "len_eq" else len(found) >= value
    if op == "contains":
        if isinstance(found, str):
            return isinstance(value, str) and value in found
        return isinstance(found, list) and any(_same(item, value) for item in found)
    return False


def _auth_problem(data):
    """Tên trường sai trong auth.yaml, rỗng nếu hợp lệ. Cùng luật với qc_agent/groundtruth/auth.py (test so hai bản)."""
    if not isinstance(data, dict) or set(data) - {"version", "login", "header", "scope"} or data.get("version") != 1:
        return "gốc"
    login = data.get("login")
    if not isinstance(login, dict) or set(login) - {"method", "path", "json", "token_path"} or login.get("method", "POST") != "POST":
        return "login"
    path = login.get("path")
    if not (isinstance(path, str) and _AUTH_PATH.fullmatch(path) and ".." not in path and not path.startswith("//")):
        return "login.path"
    if not (isinstance(login.get("token_path"), str) and _AUTH_JSONPATH.fullmatch(login["token_path"])):
        return "login.token_path"
    body = login.get("json", {})
    if not isinstance(body, dict):
        return "login.json"
    for key, value in body.items():
        if not (isinstance(key, str) and _AUTH_KEY.fullmatch(key)):
            return "login.json"
        if isinstance(value, dict):
            if set(value) != {"env"} or not (isinstance(value["env"], str) and _AUTH_ENV.fullmatch(value["env"])):
                return "login.json"
        elif not isinstance(value, (str, int, float, bool, type(None))):
            return "login.json"
    header = data.get("header", {})
    if not isinstance(header, dict) or set(header) - {"name", "scheme"}:
        return "header"
    name, scheme = header.get("name", "Authorization"), header.get("scheme", "Bearer")
    if not (isinstance(name, str) and _AUTH_HEADER.fullmatch(name)) or not (scheme == "" or (isinstance(scheme, str) and _AUTH_SCHEME.fullmatch(scheme))):
        return "header"
    if data.get("scope", "session") not in ("session", "case"):
        return "scope"
    return ""


def _auth_profile():
    if "auth" not in _cache:
        profile = None
        if AUTH_FILE.is_file():
            try:
                profile = yaml.safe_load(AUTH_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as error:
                _fatal(f"không đọc được {AUTH_FILE.name} ({type(error).__name__})")
            problem = _auth_problem(profile)
            if problem:
                _fatal(f"{AUTH_FILE.name} sai ở {problem}")
        _cache["auth"] = profile
    return _cache["auth"]


def _login(client, base, profile):
    """Đăng nhập một lần, trả token. Mọi thông điệp lỗi chỉ nêu tên biến hoặc mã HTTP, không bao giờ nêu mật khẩu hay token."""
    login, body = profile["login"], {}
    for key, value in (login.get("json") or {}).items():
        if isinstance(value, dict):
            name = value["env"]
            value = os.environ.get(name, "")
            if not value:
                _fatal(f"auth: thiếu biến môi trường {name}")
        body[key] = value
    try:
        response = client.request("POST", base + login["path"], content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                  headers={"Content-Type": "application/json"})
    except httpx.HTTPError as error:
        _fatal(f"auth: không gọi được endpoint đăng nhập ({type(error).__name__})")
    if not 200 <= response.status_code < 300:
        _fatal(f"auth: đăng nhập thất bại (HTTP {response.status_code})")
    try:
        token = _lookup(response.json(), login["token_path"])
    except ValueError:
        token = _MISSING
    if not isinstance(token, str) or not token or len(token) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in token):
        _fatal("auth: response đăng nhập không có token hợp lệ tại token_path")
    return token


class _Auth:
    """Gắn token theo auth.yaml (nếu có) vào request. Token chỉ nằm trong bộ nhớ của tiến trình test."""

    def __init__(self, base):
        self.base, self.profile, self.token = base, _auth_profile(), None

    def headers(self, client, method, template, given):
        send = {name: value for name, value in given.items() if value != ""}   # rỗng = không gửi
        profile = self.profile
        if profile is None:
            return send
        header = profile.get("header") or {}
        name, scheme = header.get("name", "Authorization"), header.get("scheme", "Bearer")
        if any(key.lower() == name.lower() for key in given) or (method == "POST" and template == profile["login"]["path"]):
            return send   # TC tự nêu header đó (rỗng hoặc giá trị khác), hoặc đang gọi chính endpoint đăng nhập
        if profile.get("scope", "session") == "session":
            if "token" not in _cache:
                _cache["token"] = _login(client, self.base, profile)
            token = _cache["token"]
        else:
            if self.token is None:
                self.token = _login(client, self.base, profile)
            token = self.token
        send[name] = f"{scheme} {token}" if scheme else token
        return send


def _run_step(client, base, tc_id, number, step, variables, auth):
    def fail(message):
        pytest.fail(f"{tc_id} bước {number}: {message}", pytrace=False)

    request, expect = step["request"], step["expect"]
    method, template = str(request["method"]).upper(), request["path"]
    params = {name: _interpolate(str(value), variables, fail) for name, value in (request.get("path_params") or {}).items()}

    def fill(match):
        if match.group(1) not in params:
            fail(f"path {template} thiếu path_params.{match.group(1)}")
        return urllib.parse.quote(params[match.group(1)], safe="")

    query = {name: (_interpolate(value, variables, fail) if isinstance(value, str) else value) for name, value in (request.get("query") or {}).items()}
    headers = auth.headers(client, method, template, dict(request.get("headers") or {}))
    kwargs = {}
    if "json" in request:
        kwargs["content"] = json.dumps(_fill(request["json"], variables, fail), ensure_ascii=False).encode("utf-8")
        if not any(name.lower() == "content-type" for name in headers):
            headers["Content-Type"] = "application/json"
    try:
        response = client.request(method, base + _PLACEHOLDER.sub(fill, template), params=query or None, headers=headers or None, **kwargs)
    except httpx.HTTPError as error:
        fail(f"không gọi được {method} {template} ({type(error).__name__})")
    if response.status_code not in expect["status"]:
        fail(f"{method} {template} trả {response.status_code}, kỳ vọng {expect['status']}")
    assertions, captures = expect.get("json") or [], step.get("capture") or {}
    if not assertions and not captures:
        return
    try:
        body = response.json()
    except ValueError:
        fail(f"{method} {template}: response không phải JSON")
    for assertion in assertions:
        found = _lookup(body, assertion["path"])
        if not _holds(assertion["op"], found, assertion.get("value")):
            fail(f"{assertion['path']} {assertion['op']} {_show(assertion.get('value'))}, thực tế {_show(found)}")
    for name, path in captures.items():
        found = _lookup(body, path)
        if found is _MISSING:
            fail(f"không capture được {name} từ {path}")
        variables[name] = found


@pytest.fixture
def gt_run():
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")

    def run(tc):
        tc_id = str(tc.get("tc_id"))
        variables, auth = {}, _Auth(base)
        try:
            with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False) as client:
                for number, step in enumerate(tc["steps"], 1):
                    _run_step(client, base, tc_id, number, step, variables, auth)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            pytest.fail(f"{tc_id}: test case sai cấu trúc ({type(error).__name__}); chạy `qc-agent gt validate`", pytrace=False)
    return run
