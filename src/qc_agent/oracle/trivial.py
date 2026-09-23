"""oracle.kind = "trivial": luôn pass. Dành cho task chỉ cần worker chạy xong (vd. demo.echo), không có ngưỡng nào để so."""
from qc_agent.oracle import OracleOutcome, register


@register("trivial")
def trivial(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
    return OracleOutcome(value="pass")
