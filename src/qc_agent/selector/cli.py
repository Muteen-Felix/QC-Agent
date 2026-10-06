"""Lenh select tao artifact selection.json truoc gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from qc_agent import settings
from qc_agent.core import project as project_lib, registry
from qc_agent.core.plan import PlanError
from qc_agent.selector import agent, pruner, rules


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PlanError("tham so select sai")


def main(argv: list[str]) -> int:
    parser = _Parser(prog="qc-agent select")
    for name in ("project", "mode", "sut-root", "base", "head", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--projects-dir")
    parser.add_argument("--workers-dir", action="append")
    try:
        args = parser.parse_args(argv)
        root = Path(args.sut_root).resolve()
        cfg, project_info = project_lib.resolve_project(args.project, args.projects_dir or settings.get().resolved_projects_dir)
        suites = project_lib.load_suites(root / cfg["suites_dir"])
        workers = registry.load_many(args.workers_dir or settings.get().workers_dirs)
        suite_map = project_lib.suites_by_worker(cfg, args.mode, suites, workers)
        policy = cfg["modes"].get(args.mode)
        if policy is None:
            raise PlanError("mode khong ton tai")
        if any(worker not in suite_map for worker in policy.get("floor_workers", [])):
            raise PlanError("floor worker khong co suite trong policy")
        diff = pruner.prune(root, args.base, args.head)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        map_path = root / ".qc-agent" / "ground-truth" / "module-map.yaml"
        module_map = yaml.safe_load(map_path.read_text(encoding="utf-8")) if map_path.is_file() else None
        decision = rules.decide([rules.ChangedFile(item.path, item.status) for item in diff.files], policy, module_map, suite_map)
        selection = agent.select(diff, decision, policy, suite_map, module_map, egress_dir=out.parent, policy_sha=project_info["sha256"])
        engine_schema = settings.get().resolved_schemas_dir / "selection.json"
        from jsonschema import Draft202012Validator
        Draft202012Validator(json.loads(engine_schema.read_text(encoding="utf-8"))).validate(selection)
        out.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"select: {selection['source']} {len(selection['suites'])} suites")
        return 0
    except (PlanError, ValueError, OSError, KeyError, registry.ManifestError) as error:
        print(f"select: loi cau hinh: {type(error).__name__}", file=sys.stderr)
        return 3
