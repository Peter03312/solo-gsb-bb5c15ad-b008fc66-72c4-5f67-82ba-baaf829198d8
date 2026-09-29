"""POST /schedule-discussions 的端到端测试。"""

from __future__ import annotations

import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post(body: dict) -> tuple[int, dict]:
    resp = client.post("/schedule-discussions", json=body)
    return resp.status_code, resp.json()


def _assign(pairs: dict[str, list[str]]) -> list[dict]:
    return [
        {"manuscript_id": mid, "reviewer_ids": rids}
        for mid, rids in pairs.items()
    ]


def _slots(caps: dict[str, int]) -> list[dict]:
    return [{"id": sid, "capacity": cap} for sid, cap in caps.items()]


def _avail(av: dict[str, list[str]]) -> list[dict]:
    return [
        {"reviewer_id": rid, "slot_ids": sids}
        for rid, sids in av.items()
    ]


def _body(
    pairs: dict[str, list[str]],
    caps: dict[str, int],
    av: dict[str, list[str]],
) -> dict:
    return {
        "assignments": _assign(pairs),
        "slots": _slots(caps),
        "reviewer_availability": _avail(av),
    }


def _schedule(pairs, caps, av) -> dict[str, str]:
    status, data = _post(_body(pairs, caps, av))
    assert status == 200, data
    assert data["status"] == "feasible", data
    return {item["manuscript_id"]: item["slot_id"] for item in data["schedule"]}


# ---------- 可行：共同出席 + 容量 + 评审人席位 ----------


def test_single_manuscript_goes_to_smallest_slot_id():
    # 字典序最小：m1 可去 s1/s2 时取 s1
    assert _schedule({"m1": ["r1"]}, {"s1": 1, "s2": 1}, {"r1": ["s1", "s2"]}) == {
        "m1": "s1"
    }


def test_common_availability_is_the_intersection():
    # m1 有 r1、r2 两位评审人：时段必须是两人共同可出席（交集）
    assert _schedule(
        {"m1": ["r1", "r2"]},
        {"s1": 1, "s2": 1},
        {"r1": ["s1", "s2"], "r2": ["s2"]},
    ) == {"m1": "s2"}


def test_reviewer_one_discussion_per_slot_forces_separation():
    # m1、m2 共享 r2；r2 只能 s1，s1 容量 2 -> 同评审人同时段只能一稿
    status, data = _post(_body(
        {"m1": ["r1", "r2"], "m2": ["r2", "r3"]},
        {"s1": 2},
        {"r1": ["s1"], "r2": ["s1"], "r3": ["s1"]},
    ))
    assert status == 200
    assert data == {"status": "infeasible", "schedule": None}


def test_separate_reviewer_sets_may_share_a_slot():
    # 不同评审人集合：同一时段可并行讨论（受容量约束）
    assert _schedule(
        {"m1": ["r1"], "m2": ["r2"]},
        {"s1": 2},
        {"r1": ["s1"], "r2": ["s1"]},
    ) == {"m1": "s1", "m2": "s1"}


def test_slot_capacity_enforced():
    # 三篇互不相关的稿件，唯一时段容量 2 -> 不可行
    status, data = _post(_body(
        {"m1": ["r1"], "m2": ["r2"], "m3": ["r3"]},
        {"s1": 2},
        {"r1": ["s1"], "r2": ["s1"], "r3": ["s1"]},
    ))
    assert status == 200
    assert data["status"] == "infeasible"
    assert data["schedule"] is None


def test_capacity_zero_slot_is_unusable():
    # m1 只被声明可出席容量为 0 的 s1 -> 不可行
    status, data = _post(_body(
        {"m1": ["r1"]},
        {"s1": 0, "s2": 1},
        {"r1": ["s1"]},
    ))
    assert status == 200
    assert data["status"] == "infeasible"


def test_manuscript_without_reviewers_only_capacity_constrained():
    # 无评审人的稿件不受出席约束，仅受容量限制
    assert _schedule({"m1": []}, {"s1": 0, "s2": 1}, {}) == {"m1": "s2"}


def test_empty_assignments_yields_empty_feasible_schedule():
    status, data = _post({
        "assignments": [],
        "slots": [{"id": "s1", "capacity": 1}],
        "reviewer_availability": [],
    })
    assert status == 200
    assert data == {"status": "feasible", "schedule": []}


def test_no_slots_with_manuscripts_is_infeasible_not_error():
    # 空时段列表属于合法输入，但无法排入任何稿件 -> infeasible（不是 422）
    status, data = _post({"assignments": _assign({"m1": ["r1"]}), "slots": [],
                          "reviewer_availability": _avail({"r1": []})})
    assert status == 200
    assert data["status"] == "infeasible"


def test_reviewer_with_empty_availability_blocks_their_manuscripts():
    # r1 显式声明无任何可出席时段：含 r1 的稿件无法排入（r2 不受影响）
    body = _body(
        {"m1": ["r1"], "m2": ["r2"]},
        {"s1": 2},
        {"r1": [], "r2": ["s1"]},
    )
    status, data = _post(body)
    assert status == 200
    assert data["status"] == "infeasible"


def test_full_schedule_example():
    # m1 的 r1/r2 共同只能 s1；m2 评审 r2/r3，r3 只能 s2；
    # s1、s2 容量均 1 -> 唯一可行的完整排期
    assert _schedule(
        {
            "m1": ["r1", "r2"],
            "m2": ["r2", "r3"],
        },
        {"s1": 1, "s2": 1},
        {
            "r1": ["s1"],
            "r2": ["s1", "s2"],
            "r3": ["s2"],
        },
    ) == {"m1": "s1", "m2": "s2"}


def test_schedule_output_sorted_by_manuscript_id():
    status, data = _post(_body(
        {"m3": ["r3"], "m1": ["r1"], "m2": ["r2"]},
        {"s1": 3},
        {"r1": ["s1"], "r2": ["s1"], "r3": ["s1"]},
    ))
    assert status == 200
    assert [item["manuscript_id"] for item in data["schedule"]] == ["m1", "m2", "m3"]


# ---------- 字典序最小：需要回溯才能找到 ----------


def test_lexicographic_minimum_requires_backtracking():
    # m1 单独含 r1（s1/s2 均可）；m2 含 r1 与 r2，而 r2 只能 s1。
    # m2 因此只能去 s1；若 m1 贪心选 s1 会与 m2 争用 r1（尽管容量为 2），
    # 故字典序最小完整解为 m1=s2、m2=s1，而不是两篇都试 s1。
    assert _schedule(
        {"m1": ["r1"], "m2": ["r1", "r2"]},
        {"s1": 2, "s2": 2},
        {"r1": ["s1", "s2"], "r2": ["s1"]},
    ) == {"m1": "s2", "m2": "s1"}


def test_lexicographic_minimum_fills_slots_in_id_order():
    # 三篇独立稿件、三个容量 1 的时段、全员全时段可出席：
    # 字典序最小完整解即按稿件顺序依次取 s1、s2、s3
    assert _schedule(
        {"m1": ["r1"], "m2": ["r2"], "m3": ["r3"]},
        {"s1": 1, "s2": 1, "s3": 1},
        {"r1": ["s1", "s2", "s3"], "r2": ["s1", "s2", "s3"], "r3": ["s1", "s2", "s3"]},
    ) == {"m1": "s1", "m2": "s2", "m3": "s3"}


def test_lexicographic_order_uses_slot_ids_not_input_order():
    # 时段按 ID 字典序比较，与输入列表顺序无关：s2 < s9
    body = _body(
        {"m1": ["r1"], "m2": ["r2"]},
        {"s9": 2, "s2": 2},
        {"r1": ["s2", "s9"], "r2": ["s2", "s9"]},
    )
    body["slots"] = list(reversed(body["slots"]))
    status, data = _post(body)
    assert status == 200
    schedule = {x["manuscript_id"]: x["slot_id"] for x in data["schedule"]}
    assert schedule == {"m1": "s2", "m2": "s2"}


# ---------- 输入列表重排不改变结果 ----------


def test_shuffling_all_lists_preserves_result():
    # 每篇两位评审人、5 个时段容量均为 5（容量不构成约束），出席模式有起伏，
    # 但总席位充足 -> 可行；任意列表重排都应得到逐字节相同的字典序最小解
    body = _body(
        {
            "m0": ["r0", "r1"],
            "m1": ["r1", "r2"],
            "m2": ["r0", "r2"],
            "m3": ["r0", "r1"],
            "m4": ["r1", "r2"],
        },
        {f"s{j}": 5 for j in range(5)},
        {
            "r0": ["s0", "s1", "s2", "s3", "s4"],
            "r1": ["s1", "s2", "s3", "s4"],
            "r2": ["s0", "s2", "s3", "s4"],
        },
    )
    status, expected = _post(body)
    assert status == 200 and expected["status"] == "feasible", expected

    shuffled = {
        "assignments": [
            {**a, "reviewer_ids": sorted(a["reviewer_ids"], reverse=True)}
            for a in random.Random(1).sample(body["assignments"], len(body["assignments"]))
        ],
        "slots": list(reversed(body["slots"])),
        "reviewer_availability": list(reversed(body["reviewer_availability"])),
    }
    status, data = _post(shuffled)
    assert status == 200
    assert data == expected


def test_duplicate_reviewer_ids_within_assignment_are_deduplicated():
    # 名单内重复评审人 ID 不产生额外席位争用，按同一评审人处理
    status, data = _post(_body(
        {"m1": ["r1", "r1"], "m2": ["r2"]},
        {"s1": 1, "s2": 1},
        {"r1": ["s1"], "r2": ["s2"]},
    ))
    assert status == 200
    schedule = {x["manuscript_id"]: x["slot_id"] for x in data["schedule"]}
    assert schedule == {"m1": "s1", "m2": "s2"}


def test_duplicate_slot_ids_in_availability_are_deduplicated():
    body = _body({"m1": ["r1"]}, {"s1": 1}, {"r1": ["s1", "s1"]})
    status, data = _post(body)
    assert status == 200
    assert data["schedule"] == [{"manuscript_id": "m1", "slot_id": "s1"}]


# ---------- 非法输入：422 结构化错误 ----------


def _error_types(body: dict) -> list[str]:
    status, data = _post(body)
    assert status == 422, data
    assert "schedule" not in data
    return [e["type"] for e in data["errors"]]


def test_too_many_manuscripts():
    body = {
        "assignments": [
            {"manuscript_id": f"m{i}", "reviewer_ids": []} for i in range(9)
        ],
        "slots": [{"id": "s1", "capacity": 9}],
        "reviewer_availability": [],
    }
    assert _error_types(body) == ["too_many_manuscripts"]


def test_too_many_slots():
    body = {
        "assignments": [],
        "slots": [{"id": f"s{i}", "capacity": 1} for i in range(9)],
        "reviewer_availability": [],
    }
    assert _error_types(body) == ["too_many_slots"]


def test_limit_of_eight_is_accepted():
    body = {
        "assignments": [
            {"manuscript_id": f"m{i}", "reviewer_ids": [f"r{i}"]}
            for i in range(8)
        ],
        "slots": [{"id": f"s{i}", "capacity": 1} for i in range(8)],
        "reviewer_availability": [
            {"reviewer_id": f"r{i}", "slot_ids": [f"s{i}"]} for i in range(8)
        ],
    }
    status, data = _post(body)
    assert status == 200
    assert data["status"] == "feasible"
    assert len(data["schedule"]) == 8


def test_duplicate_manuscript_id_rejected():
    body = _body(
        {"m1": ["r1"]},
        {"s1": 1},
        {"r1": ["s1"], "r2": ["s1"]},
    )
    body["assignments"].append({"manuscript_id": "m1", "reviewer_ids": ["r2"]})
    types = _error_types(body)
    assert types == ["duplicate_manuscript"]


def test_duplicate_slot_id_rejected():
    body = {
        "assignments": [],
        "slots": [
            {"id": "s1", "capacity": 1},
            {"id": "s1", "capacity": 2},
        ],
        "reviewer_availability": [],
    }
    assert _error_types(body) == ["duplicate_slot"]


def test_duplicate_availability_entry_rejected():
    body = _body({"m1": ["r1"]}, {"s1": 1}, {"r1": ["s1"]})
    body["reviewer_availability"].append({"reviewer_id": "r1", "slot_ids": []})
    assert _error_types(body) == ["duplicate_availability"]


def test_reviewer_in_assignment_without_availability_declaration():
    body = _body(
        {"m1": ["r1", "r2"]},
        {"s1": 1},
        {"r1": ["s1"]},  # r2 未声明
    )
    status, data = _post(body)
    assert status == 422
    errors = data["errors"]
    assert [e["type"] for e in errors] == ["reviewer_availability_undeclared"]
    assert errors[0]["reviewer_id"] == "r2"


def test_availability_declaration_alone_is_allowed():
    # 声明了可出席时段但未参与任何稿件的评审人是多余信息，不构成错误
    status, data = _post(_body(
        {"m1": ["r1"]}, {"s1": 1}, {"r1": ["s1"], "r9": ["s1"]},
    ))
    assert status == 200
    assert data["status"] == "feasible"


def test_unknown_slot_reference_rejected():
    body = _body(
        {"m1": ["r1"]},
        {"s1": 1},
        {"r1": ["s1", "s404"]},
    )
    status, data = _post(body)
    assert status == 422
    (error,) = data["errors"]
    assert error["type"] == "unknown_slot_reference"
    assert error["slot_id"] == "s404"
    assert error["reviewer_id"] == "r1"


def test_multiple_errors_collected_and_deterministically_sorted():
    # 同时触发：重复稿件、未声明评审人、引用未知时段；错误一次性收集
    body = {
        "assignments": [
            {"manuscript_id": "m1", "reviewer_ids": ["r1"]},
            {"manuscript_id": "m1", "reviewer_ids": ["r2"]},
        ],
        "slots": [{"id": "s1", "capacity": 1}],
        "reviewer_availability": [
            {"reviewer_id": "r1", "slot_ids": ["s1", "sx"]},
        ],
    }
    status, data_a = _post(body)
    assert status == 422
    types_a = [e["type"] for e in data_a["errors"]]
    assert set(types_a) == {
        "duplicate_manuscript",
        "reviewer_availability_undeclared",
        "unknown_slot_reference",
    }
    # 确定性：同样输入重复请求结果逐字节相同
    status, data_b = _post(body)
    assert data_a == data_b


def test_error_order_does_not_depend_on_input_order():
    body = {
        "assignments": [
            {"manuscript_id": "m2", "reviewer_ids": ["r9"]},
            {"manuscript_id": "m2", "reviewer_ids": []},
            {"manuscript_id": "m1", "reviewer_ids": []},
            {"manuscript_id": "m1", "reviewer_ids": []},
        ],
        "slots": [{"id": "s1", "capacity": 1}],
        "reviewer_availability": [
            {"reviewer_id": "r9", "slot_ids": ["zz"]},
        ],
    }
    _, data = _post(body)
    assert [e["type"] for e in data["errors"]] == sorted(
        e["type"] for e in data["errors"]
    )
    # duplicate_manuscript 按稿件 ID 排序：m1 在 m2 前
    dup_ids = [e["manuscript_id"] for e in data["errors"] if e["type"] == "duplicate_manuscript"]
    assert dup_ids == ["m1", "m2"]


def test_negative_capacity_is_field_validation_error():
    # 非负整数容量由请求模型校验，返回 422（FastAPI 字段错误形态）
    status, data = _post({
        "assignments": [],
        "slots": [{"id": "s1", "capacity": -1}],
    })
    assert status == 422
    assert "detail" in data


def test_extra_field_rejected():
    status, data = _post({
        "assignments": [],
        "slots": [{"id": "s1", "capacity": 1, "unexpected": True}],
    })
    assert status == 422


def test_missing_required_field_rejected():
    status, data = _post({"assignments": []})  # 缺 slots
    assert status == 422


# ---------- 上限规模：8 稿 / 8 时段的完整可行与不可行 ----------


def test_max_size_feasible_schedule():
    # 8 篇稿件各有独立评审人，8 个时段各容量 1，全员全时段可出席
    body = {
        "assignments": [
            {"manuscript_id": f"m{i}", "reviewer_ids": [f"r{i}"]} for i in range(8)
        ],
        "slots": [{"id": f"s{i}", "capacity": 1} for i in range(8)],
        "reviewer_availability": [
            {"reviewer_id": f"r{i}", "slot_ids": [f"s{k}" for k in range(8)]}
            for i in range(8)
        ],
    }
    status, data = _post(body)
    assert status == 200
    schedule = {x["manuscript_id"]: x["slot_id"] for x in data["schedule"]}
    assert schedule == {f"m{i}": f"s{i}" for i in range(8)}


def test_max_size_shared_reviewer_with_too_few_slots_infeasible():
    # 8 篇共享同一评审人，该评审人只可出席 7 个时段 -> 同时段只能一稿，不可行
    body = {
        "assignments": [
            {"manuscript_id": f"m{i}", "reviewer_ids": ["r1"]} for i in range(8)
        ],
        "slots": [{"id": f"s{i}", "capacity": 8} for i in range(7)],
        "reviewer_availability": [
            {"reviewer_id": "r1", "slot_ids": [f"s{i}" for i in range(7)]}
        ],
    }
    status, data = _post(body)
    assert status == 200
    assert data == {"status": "infeasible", "schedule": None}
