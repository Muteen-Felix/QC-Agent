"""`qc-agent doctor`: bảng probe + exit code. Manifest giả qua --workers-dir, không cần công cụ thật (cùng cách với test_registry)."""
import sys
from pathlib import Path

import pytest
import yaml

from qc_agent.core import cli, doctor, registry


def write_worker(folder: Path, name: str, **overrides) -> None:
    data = {
        "name": name,
        "version_probe": [sys.executable, "--version"],
        "adapter": f"qc_agent/adapters/{name}_adapter.py",
        "lanes": ["gate"],
        "capabilities": [{"id": "demo.echo", "oracle_kinds": ["trivial"]}],
        "requires": {"env": [], "binaries": []},
        "data_egress": [],
    }
    data.update(overrides)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def rows(out: str) -> dict[str, str]:
    """tên worker -> cả dòng, bỏ dòng tiêu đề và dòng tổng."""
    lines = [line for line in out.splitlines() if line.strip()][1:-1]
    return {line.split()[0]: line for line in lines}


def test_all_probes_ok_exit_0(tmp_path, capsys):
    write_worker(tmp_path, "alpha")
    write_worker(tmp_path, "beta", lanes=["gate", "discovery"])
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    table = rows(out)
    assert set(table) == {"alpha", "beta"}
    assert "✅" in table["alpha"] and "Python" in table["alpha"]      # version lấy từ stdout của version_probe
    assert "gate,discovery" in table["beta"]
    assert "2/2 worker OK" in out and "❌" not in out


def test_missing_binary_is_reported_with_reason_and_exit_1(tmp_path, capsys):
    write_worker(tmp_path, "alpha")
    write_worker(tmp_path, "playwright", requires={"env": [], "binaries": ["qc-no-such-binary-xyz"]})
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    table = rows(out)
    assert "✅" in table["alpha"]
    assert "❌" in table["playwright"] and "qc-no-such-binary-xyz" in table["playwright"]
    assert table["playwright"].split()[1] == "-"          # probe hỏng: không có version
    assert "1/2 worker OK" in out


def test_failing_version_probe_exit_1(tmp_path, capsys):
    write_worker(tmp_path, "sick", version_probe=[sys.executable, "-c", "import sys; sys.stderr.write('hỏng\\n'); sys.exit(7)"])
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 1
    assert "exit=7" in rows(capsys.readouterr().out)["sick"]


def test_missing_env_is_reported(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("QC_DOCTOR_TEST_ENV", raising=False)
    write_worker(tmp_path, "needs_env", requires={"env": ["QC_DOCTOR_TEST_ENV"], "binaries": []})
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 1
    assert "QC_DOCTOR_TEST_ENV" in capsys.readouterr().out


def test_no_workers_is_not_green(tmp_path, capsys):
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 1          # "không kiểm được gì" không được ra exit 0
    assert "không tìm thấy manifest" in capsys.readouterr().out


def test_uses_registry_probe(tmp_path, capsys, monkeypatch):
    """doctor không có logic probe riêng: kết quả của registry.probe là kết quả của doctor."""
    write_worker(tmp_path, "alpha")

    def fake_probe(worker):
        worker.probe_ok, worker.probe_reason, worker.version = False, "do registry.probe", None
        return worker

    monkeypatch.setattr(registry, "probe", fake_probe)
    assert doctor.main(["--workers-dir", str(tmp_path)]) == 1
    assert "do registry.probe" in capsys.readouterr().out


def test_wired_into_cli_main(tmp_path, capsys):
    write_worker(tmp_path, "alpha")
    assert cli.main(["doctor", "--workers-dir", str(tmp_path)]) == 0
    assert "1/1 worker OK" in capsys.readouterr().out


def test_bad_manifest_is_system_error_via_cli(tmp_path, capsys):
    (tmp_path / "broken.yaml").write_text("name: broken\n", encoding="utf-8")     # thiếu adapter/lanes/capabilities
    assert cli.main(["doctor", "--workers-dir", str(tmp_path)]) == cli.SYSTEM_ERROR


def test_bad_argument_is_system_error_via_cli(capsys):
    assert cli.main(["doctor", "--no-such-flag"]) == cli.SYSTEM_ERROR


def test_shipped_manifests_all_load():
    """doctor trên thư mục workers/ thật phải nạp được (probe có thể hỏng vì máy thiếu công cụ — chuyện khác)."""
    workers = registry.load_many([Path("workers")])
    assert workers and all(doctor._row(w)[0] == w.name for w in workers.values())
