"""Đọc OpenAPI/Swagger của SUT (file hoặc URL) và rút ra những gì `qc-agent init` cần biết: endpoint nên loại khỏi fuzz, endpoint GET cho k6.

Chỉ đọc cấu trúc; không gọi endpoint của SUT ngoài việc tải chính tài liệu OpenAPI khi được cho URL. Không đoán: cái không nhận ra thì bỏ và ghi cảnh báo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

MAX_BYTES = 5_000_000
METHODS = ("get", "put", "post", "delete", "options", "head", "patch")
UPLOAD_MEDIA = ("multipart/form-data", "application/octet-stream")
MAX_K6_PATHS = 3


class OpenApiError(ValueError):
    """Không đọc được hoặc không phải tài liệu OpenAPI/Swagger."""


@dataclass
class Analysis:
    title: str = ""
    version: str = ""
    prefix: str = ""                                          # tiền tố từ servers[0].url / basePath (áp cho đường dẫn k6)
    exclude: list[tuple[str, str]] = field(default_factory=list)   # (đường dẫn trong schema, lý do) — không fuzz
    get_paths: list[str] = field(default_factory=list)        # đã gồm tiền tố, đã xếp, tối đa MAX_K6_PATHS
    secured: bool = False                                     # có khai security ở đâu đó: cần cấu hình auth
    warnings: list[str] = field(default_factory=list)


def load(source: str, *, timeout: float = 15.0) -> dict:
    """`source` là đường dẫn file hoặc URL http(s)."""
    parsed = urlsplit(source)
    if parsed.scheme in ("http", "https"):
        try:
            response = httpx.get(source, timeout=timeout, follow_redirects=False)
            response.raise_for_status()
        except httpx.HTTPError as error:
            host = parsed.netloc.rpartition("@")[2]  # bỏ user:pass@; query/fragment cũng không được in
            raise OpenApiError(f"không tải được OpenAPI từ {parsed.scheme}://{host}{parsed.path}: {type(error).__name__}") from None
        raw = response.content
    elif parsed.scheme in ("", "file") or len(parsed.scheme) == 1:  # len==1: ổ đĩa Windows (C:\...)
        path = Path(source if parsed.scheme != "file" else parsed.path)
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise OpenApiError(f"không đọc được file OpenAPI {path}: {error.strerror or type(error).__name__}") from None
    else:
        raise OpenApiError(f"nguồn OpenAPI phải là file hoặc URL http(s), nhận scheme {parsed.scheme!r}")
    if len(raw) > MAX_BYTES:
        raise OpenApiError(f"tài liệu OpenAPI vượt {MAX_BYTES} byte")
    try:
        spec = yaml.safe_load(raw.decode("utf-8-sig"))  # YAML là tập cha của JSON
    except (UnicodeError, yaml.YAMLError) as error:
        raise OpenApiError(f"tài liệu OpenAPI không phải JSON/YAML hợp lệ: {type(error).__name__}") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict) or not ("openapi" in spec or "swagger" in spec):
        raise OpenApiError("không phải OpenAPI/Swagger: thiếu khoá 'openapi'/'swagger' hoặc 'paths'")
    return spec


def _deref(spec: dict, node, depth: int = 0):
    """Giải `$ref` nội bộ (#/...). Tham chiếu ngoài hoặc vòng lặp => None (coi như không biết)."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if depth > 8 or not isinstance(ref, str) or not ref.startswith("#/"):
            return None
        target = spec
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(target, dict) else None
        node, depth = target, depth + 1
    return node


def _prefix(spec: dict, warnings: list[str]) -> str:
    if "swagger" in spec:
        base = spec.get("basePath") or ""
    else:
        servers = spec.get("servers")
        base = urlsplit(servers[0].get("url", "")).path if isinstance(servers, list) and servers and isinstance(servers[0], dict) else ""
    base = base.rstrip("/")
    if not base:
        return ""
    if "{" in base or not base.startswith("/"):
        warnings.append(f"tiền tố máy chủ {base!r} có biến hoặc không bắt đầu bằng '/': bỏ qua, đường dẫn k6 không có tiền tố")
        return ""
    return base


def _is_upload(spec: dict, op: dict) -> str | None:
    body = _deref(spec, op.get("requestBody"))
    if isinstance(body, dict) and isinstance(body.get("content"), dict):
        for media in body["content"]:
            if str(media).split(";")[0].strip().lower() in UPLOAD_MEDIA:
                return f"nhận {str(media).split(';')[0]}"
    consumes = op.get("consumes") or spec.get("consumes") or []  # Swagger 2
    if any(str(media).split(";")[0].strip().lower() in UPLOAD_MEDIA for media in consumes):
        return "nhận tệp (consumes)"
    for parameter in op.get("parameters") or []:
        parameter = _deref(spec, parameter)
        if isinstance(parameter, dict) and (parameter.get("in") == "formData" and parameter.get("type") == "file"):
            return "nhận tệp (formData)"
    return None


def _required_parameters(spec: dict, path_item: dict, op: dict) -> bool:
    for parameter in [*(path_item.get("parameters") or []), *(op.get("parameters") or [])]:
        parameter = _deref(spec, parameter)
        if not isinstance(parameter, dict):
            return True  # không giải được => coi như cần tham số (không đoán)
        if parameter.get("required") or parameter.get("in") == "path":
            return True
    return isinstance(_deref(spec, op.get("requestBody")), dict)


def _has_security(spec: dict, op: dict) -> bool:
    if "security" in op:
        return bool(op["security"])  # `security: []` ở cấp operation nghĩa là công khai
    return bool(spec.get("security"))


def analyze(spec: dict) -> Analysis:
    result = Analysis(title=str((spec.get("info") or {}).get("title") or ""), version=str((spec.get("info") or {}).get("version") or ""))
    result.prefix = _prefix(spec, result.warnings)
    candidates: list[str] = []
    excluded: dict[str, str] = {}
    for path, item in spec["paths"].items():
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(item, dict):
            continue
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            if _has_security(spec, op):
                result.secured = True
            reason = _is_upload(spec, op)
            if reason and path not in excluded:
                excluded[path] = f"{method.upper()} {reason}: fuzz tệp không có nghĩa và tốn thời gian"
            if (method == "get" and "{" not in path and not op.get("deprecated") and not _has_security(spec, op)
                    and not _required_parameters(spec, item, op)):
                candidates.append(path)
    result.exclude = sorted(excluded.items())
    ranked = sorted(set(candidates), key=lambda p: (p.count("/") if p != "/" else 0, p))
    result.get_paths = [result.prefix + p for p in ranked[:MAX_K6_PATHS]]  # "/" + tiền tố "/api" => "/api/"; không tiền tố => "/"
    if result.secured:
        result.warnings.append("API khai security: suite sinh ra KHÔNG có `inputs.auth` — thêm thủ công (Schemathesis cần header xác thực); "
                               "endpoint có security bị loại khỏi k6 smoke")
    if not result.get_paths:
        result.warnings.append("không có endpoint GET nào không cần tham số/xác thực: bỏ perf-smoke")
    return result
