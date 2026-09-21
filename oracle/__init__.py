"""Oracle: phán pass/fail từ số đo + tín hiệu mà adapter đã trích. Không biết worker nào chạy ra chúng.

Thêm một oracle.kind mới = thả một file .py vào thư mục này và dùng @register("<kind>"). File này không phải sửa.
"""
import importlib
import pkgutil
from dataclasses import dataclass, field


class OracleError(ValueError):
    """Dữ liệu đưa vào oracle không đủ/không hợp lệ để phán -> adapter đổi thành status=error (không bao giờ fail)."""


@dataclass
class OracleOutcome:
    value: str | None  # "pass" | "fail" | None (oracle không phán: chỉ hợp lệ cho discovery)
    findings: list = field(default_factory=list)  # đã đúng dạng findings[] của result.json
    notes: list = field(default_factory=list)  # chuỗi, đi vào adapter_notes


_KINDS: dict = {}


def register(kind: str):
    """Decorator: đăng ký hàm oracle(oracle_spec, metrics, signals) -> OracleOutcome cho `kind`."""

    def deco(fn):
        old = _KINDS.get(kind)
        # reload cùng một hàm thì cho qua; hai hàm khác nhau tranh một kind là lỗi lập trình, không được ghi đè im lặng
        if old is not None and (old.__module__, old.__qualname__) != (fn.__module__, fn.__qualname__):
            raise OracleError(f"oracle.kind {kind!r} đã được đăng ký bởi {old.__module__}.{old.__qualname__}")
        _KINDS[kind] = fn
        return fn

    return deco


def evaluate(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
    kind = oracle_spec.get("kind")
    if kind not in _KINDS:
        raise OracleError(f"oracle.kind chưa hỗ trợ: {kind!r} (đã đăng ký: {sorted(_KINDS)})")
    outcome = _KINDS[kind](oracle_spec, metrics, signals)
    if not isinstance(outcome, OracleOutcome) or outcome.value not in ("pass", "fail", None):
        raise OracleError(f"oracle {kind!r} trả về kết quả không hợp lệ: {outcome!r}")
    return outcome


# Tự nạp mọi module con. Lỗi import ở một oracle được để nổ to: nuốt nó thì kind đó biến mất và task chỉ báo "chưa hỗ trợ".
for _m in pkgutil.iter_modules(__path__):
    if not _m.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_m.name}")
