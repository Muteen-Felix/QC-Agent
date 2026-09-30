"""Luat tat dinh duoc ap dung truoc khi goi LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str


@dataclass(frozen=True)
class RuleDecision:
    full_set: bool
    floor_only: bool
    reason: str
    hint_workers: dict[str, tuple[str, ...]]
    unmapped: tuple[str, ...]
    module_map_status: str


def glob_to_regex(pattern: str) -> re.Pattern:
    parts = ["^"]
    i = 0
    while i < len(pattern):
        if pattern[i:i + 2] == "**":
            if pattern[i + 2:i + 3] == "/":
                parts.append("(?:.*/)?")
                i += 3
            else:
                parts.append(".*")
                i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(parts) + "$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(pattern).fullmatch(path) for pattern in patterns)


def decide(changed: list[ChangedFile], mode_policy: dict, module_map: dict | None,
           suite_map: dict[str, list[str]]) -> RuleDecision:
    status = module_map.get("status", "missing") if module_map else "missing"
    if not changed:
        return RuleDecision(False, True, "empty_diff", {}, (), status)
    for item in changed:
        if matches(item.path, mode_policy.get("full_set_paths", [])):
            return RuleDecision(True, False, "full_set_path: " + item.path, {}, (), status)
    if all(matches(item.path, mode_policy.get("docs_paths", [])) for item in changed):
        return RuleDecision(False, True, "docs_only", {}, (), status)
    hints: dict[str, list[str]] = {}
    unmapped = []
    for item in changed:
        found = False
        for module in (module_map or {}).get("modules", []):
            if matches(item.path, module["paths"]):
                found = True
                for worker, suites in suite_map.items():
                    if set(suites) & set(module["suites"]):
                        hints.setdefault(worker, []).append("module-map: " + module["name"])
        if not found:
            unmapped.append(item.path)
    return RuleDecision(False, False, "analysis", {k: tuple(dict.fromkeys(v)) for k, v in hints.items()},
                        tuple(unmapped), status)
