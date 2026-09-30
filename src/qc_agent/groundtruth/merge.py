"""Gộp catalog cũ (đã qua tay QA) với tập ứng viên mới khi PRD đổi (`gt regen`, S1-06). Hàm thuần, tất định, không LLM, không I/O.

Nguyên tắc: thứ con người đã quyết định thì KHÔNG BAO GIỜ bị máy đụng vào.
  1. Giữ NGUYÊN VĂN mọi TC `approved`, mọi TC `rejected` và mọi TC `origin: qa`. Giữ `rejected` để LLM không đề xuất lại đúng TC đã bị loại:
     `tc_id` băm theo nội dung nên TC giống hệt sẽ có cùng `tc_id` và bị bỏ ở bước 3.
  2. Bỏ các TC `draft` + `origin: llm` cũ; thay bằng ứng viên mới.
  3. Ứng viên có `tc_id` trùng với TC đang giữ thì bỏ; còn lại thêm vào với `status: draft`.
  4. `stories` lấy theo PRD mới. TC đang giữ trỏ tới AC đã mất thì KHÔNG sửa, chỉ báo trong `lost_acs`.
  5. Catalog thành `draft` nếu còn bất kỳ TC draft nào; nếu không thì giữ status cũ.
`uncovered_acs`: lý do cũ (có thể QA đã sửa) thắng lý do mới; AC đã có TC (không tính TC rejected) thì không còn là "uncovered".
"""
from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class MergeResult:
    catalog: dict
    kept: int                                   # số TC được giữ nguyên (approved / rejected / qa)
    added: tuple[str, ...]                      # tc_id ứng viên mới được thêm (draft)
    removed_drafts: tuple[str, ...]             # tc_id của TC draft+llm cũ bị thay thế
    lost_acs: dict[str, tuple[str, ...]]        # tc_id đang giữ -> AC mà PRD mới không còn
    orphans: tuple[str, ...]                    # AC không có TC (trừ rejected) và không nằm trong uncovered_acs


def protected(tc: dict) -> bool:
    return tc.get("status") in ("approved", "rejected") or tc.get("origin") == "qa"


def merge(old: dict, candidate: dict) -> MergeResult:
    kept = [copy.deepcopy(tc) for tc in old["test_cases"] if protected(tc)]
    removed = tuple(tc["tc_id"] for tc in old["test_cases"] if not protected(tc))
    kept_ids = {tc["tc_id"] for tc in kept}
    added = [copy.deepcopy(tc) for tc in candidate["test_cases"] if tc["tc_id"] not in kept_ids]

    stories = copy.deepcopy(candidate["stories"])
    position = {ac["ac_id"]: (s, a) for s, story in enumerate(stories) for a, ac in enumerate(story["acs"])}
    lost = {tc["tc_id"]: tuple(ref for ref in tc["ac_refs"] if ref not in position) for tc in kept}
    lost = {tc_id: refs for tc_id, refs in lost.items() if refs}

    def order(tc: dict):
        where = position.get(tc["ac_refs"][0])
        return (0, *where, tc["tc_id"]) if where is not None else (1, 0, 0, tc["tc_id"])   # TC mồ côi AC xếp cuối, giữ thứ tự ổn định

    test_cases = sorted([*kept, *added], key=order)
    covered = {ref for tc in test_cases if tc["status"] != "rejected" for ref in tc["ac_refs"]}
    reasons = {u["ac_id"]: u["reason"] for u in candidate["uncovered_acs"]}
    reasons.update({u["ac_id"]: u["reason"] for u in old["uncovered_acs"]})
    uncovered = [{"ac_id": ac_id, "reason": reasons[ac_id]} for ac_id in sorted(reasons, key=lambda a: position.get(a, (9999, 0)))
                 if ac_id in position and ac_id not in covered]
    listed = {u["ac_id"] for u in uncovered}
    orphans = tuple(ac_id for ac_id in position if ac_id not in covered and ac_id not in listed)
    status = "draft" if any(tc["status"] == "draft" for tc in test_cases) else old["status"]
    catalog = {"version": 1, "prd": copy.deepcopy(candidate["prd"]), "generated_by": copy.deepcopy(candidate["generated_by"]), "status": status,
               "stories": stories, "test_cases": test_cases, "uncovered_acs": uncovered}
    return MergeResult(catalog, len(kept), tuple(tc["tc_id"] for tc in added), removed, lost, orphans)
