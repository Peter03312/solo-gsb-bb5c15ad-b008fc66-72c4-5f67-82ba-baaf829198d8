"""POST /review-clock 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post(body: dict) -> tuple[int, dict]:
    resp = client.post("/review-clock", json=body)
    return resp.status_code, resp.json()


def _clocks(body: dict) -> list[dict]:
    status, data = _post(body)
    assert status == 200, data
    return data["clocks"]


PAIR_M1_R1 = {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 10}
Q = "2026-09-25T18:00:00Z"  # 周五傍晚


def _body(pairs, events, *, holidays=None, query_at=Q) -> dict:
    return {
        "pairs": pairs,
        "events": events,
        "holidays": holidays or [],
        "query_at": query_at,
    }


def _ev(mid, rid, etype, at) -> dict:
    return {"manuscript_id": mid, "reviewer_id": rid, "type": etype, "at": at}


# ---------- 基本状态与工时累计 ----------


def test_pending_without_events():
    clocks = _clocks(_body([dict(PAIR_M1_R1, budget_hours=3)], []))
    assert clocks == [{
        "manuscript_id": "m1",
        "reviewer_id": "r1",
        "status": "pending",
        "worked_hours": 0,
        "remaining_hours": 3,
        "deadline": None,
        "overdue": False,
    }]


def test_accept_counts_only_working_window():
    # 周一 10:00 接受，查询周一 12:00：已用 2h，剩余 8h
    events = [_ev("m1", "r1", "accept", "2026-09-21T10:00:00Z")]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-21T12:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "active"
    assert c["worked_hours"] == 2
    assert c["remaining_hours"] == 8
    # 10h 预算：周一剩 7h（10-17）+ 周二 3h -> 周二 12:00
    assert c["deadline"] == "2026-09-22T12:00:00Z"
    assert c["overdue"] is False


def test_work_stops_at_17_and_resumes_next_day():
    # 周一 16:00 接受；查询周二 18:00：周一 1h + 周二 8h = 9h
    events = [_ev("m1", "r1", "accept", "2026-09-21T16:00:00Z")]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-22T18:00:00Z")
    )
    c = clocks[0]
    assert c["worked_hours"] == 9
    assert c["remaining_hours"] == 1
    # 10h 预算从周一 16:00：1h 周一 + 8h 周二 + 1h 周三 -> 周三 10:00
    assert c["deadline"] == "2026-09-23T10:00:00Z"
    assert c["overdue"] is False


def test_accept_before_09_clamped_to_window_start():
    events = [_ev("m1", "r1", "accept", "2026-09-21T08:00:00Z")]
    clocks = _clocks(
        _body([dict(PAIR_M1_R1, budget_hours=8)], events,
              query_at="2026-09-21T08:30:00Z")
    )
    c = clocks[0]
    assert c["worked_hours"] == 0
    assert c["deadline"] == "2026-09-21T17:00:00Z"


def test_accept_on_weekend_starts_monday():
    # 周六接受 -> 计时从周一 09:00 开始
    events = [_ev("m1", "r1", "accept", "2026-09-26T12:00:00Z")]
    clocks = _clocks(
        _body([dict(PAIR_M1_R1, budget_hours=1)], events,
              query_at="2026-09-28T10:30:00Z")
    )
    c = clocks[0]
    assert c["deadline"] == "2026-09-28T10:00:00Z"
    assert c["worked_hours"] == 1.5
    assert c["overdue"] is True


def test_fractional_hours_rounded_to_seconds():
    # 接受周一 09:00:00，查询周一 09:00:05 -> 5 秒 = 1/720 小时
    events = [_ev("m1", "r1", "accept", "2026-09-21T09:00:00Z")]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-21T09:00:05Z")
    )
    c = clocks[0]
    assert c["worked_hours"] == round(5 / 3600, 6)
    assert isinstance(c["worked_hours"], float)


# ---------- 暂停 / 恢复 ----------


def test_pause_freezes_work_and_clears_deadline():
    events = [
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "pause", "2026-09-21T12:00:00Z"),
    ]
    # 查询时刻在很久以后（跨多个工作日）：工时与状态冻结在暂停时
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-25T18:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "paused"
    assert c["worked_hours"] == 2
    assert c["remaining_hours"] == 8
    assert c["deadline"] is None
    assert c["overdue"] is False


def test_resume_reprojects_deadline_with_remaining_budget():
    # 周一 10-12 工作 2h 后暂停；周二 10:00 恢复；查询周二 11:00（已用 3h）
    events = [
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "pause", "2026-09-21T12:00:00Z"),
        _ev("m1", "r1", "resume", "2026-09-22T10:00:00Z"),
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-22T11:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "active"
    assert c["worked_hours"] == 3
    assert c["remaining_hours"] == 7
    # 恢复时剩 8h：周二 10-17 共 7h + 周三 1h -> 周三 10:00
    assert c["deadline"] == "2026-09-23T10:00:00Z"
    assert c["overdue"] is False


def test_pause_over_weekend_does_not_consume_budget():
    # 周五 16:00 接受、16:30 暂停（0.5h），周一 10:00 恢复后立即查询
    events = [
        _ev("m1", "r1", "accept", "2026-09-25T16:00:00Z"),
        _ev("m1", "r1", "pause", "2026-09-25T16:30:00Z"),
        _ev("m1", "r1", "resume", "2026-09-28T10:00:00Z"),
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-28T10:00:00Z")
    )
    c = clocks[0]
    assert c["worked_hours"] == 0.5
    assert c["remaining_hours"] == 9.5
    # 9.5h 从周一 10:00：7h 周一 + 2.5h 周二 -> 周二 11:30
    assert c["deadline"] == "2026-09-29T11:30:00Z"


# ---------- 节假日 ----------


def test_holiday_is_skipped_like_weekend():
    # 周五 16:00 接受、预算 10h；周一为节假日：1h 周五 + 8h 周二 + 1h 周三
    events = [_ev("m1", "r1", "accept", "2026-09-25T16:00:00Z")]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, holidays=["2026-09-28"],
              query_at="2026-09-25T16:00:00Z")
    )
    assert clocks[0]["deadline"] == "2026-09-30T10:00:00Z"


def test_duplicate_holidays_deduped():
    events = [_ev("m1", "r1", "accept", "2026-09-25T16:00:00Z")]
    body = _body(
        [PAIR_M1_R1], events,
        holidays=["2026-09-28", "2026-09-28"],
        query_at="2026-09-25T16:00:00Z",
    )
    status, data = _post(body)
    assert status == 200
    assert data["holidays"] == ["2026-09-28"]


# ---------- 提交与逾期 ----------


def test_submit_on_time_judged_by_submission_instant():
    # 周一 10:00 接受、预算 10h；截止周二 12:00；周二 12:00 整点提交 -> 未逾期
    events = [
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "submit", "2026-09-22T12:00:00Z"),
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-25T18:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "submitted"
    assert c["worked_hours"] == 10
    assert c["remaining_hours"] == 0
    assert c["deadline"] == "2026-09-22T12:00:00Z"
    assert c["overdue"] is False


def test_submit_late_is_overdue_even_if_query_later():
    events = [
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "submit", "2026-09-22T12:00:05Z"),  # 晚 5 秒
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-10-01T00:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "submitted"
    assert c["overdue"] is True
    assert c["worked_hours"] == round(10 + 5 / 3600, 6)
    assert c["remaining_hours"] == round(-5 / 3600, 6)


def test_active_overdue_judged_at_query_instant():
    # 预算 1h：周一 09:00 接受 -> 截止 10:00；查询 10:00:01
    events = [_ev("m1", "r1", "accept", "2026-09-21T09:00:00Z")]
    clocks = _clocks(
        _body([dict(PAIR_M1_R1, budget_hours=1)], events,
              query_at="2026-09-21T10:00:01Z")
    )
    c = clocks[0]
    assert c["status"] == "active"
    assert c["overdue"] is True
    assert c["remaining_hours"] == round(-1 / 3600, 6)


def test_deadline_equal_query_is_not_overdue():
    events = [_ev("m1", "r1", "accept", "2026-09-21T09:00:00Z")]
    clocks = _clocks(
        _body([dict(PAIR_M1_R1, budget_hours=1)], events,
              query_at="2026-09-21T10:00:00Z")
    )
    assert clocks[0]["overdue"] is False


# ---------- 查询时刻语义 ----------


def test_events_after_query_at_are_ignored():
    # 查询周一 12:00；提交事件在周二 -> 整段忽略，状态仍 active
    events = [
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "submit", "2026-09-22T12:00:00Z"),
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], events, query_at="2026-09-21T12:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "active"
    assert c["worked_hours"] == 2
    assert c["deadline"] == "2026-09-22T12:00:00Z"


def test_events_can_be_supplied_out_of_order():
    ordered = [
        _ev("m1", "r1", "resume", "2026-09-22T10:00:00Z"),
        _ev("m1", "r1", "pause", "2026-09-21T12:00:00Z"),
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
    ]
    clocks = _clocks(
        _body([PAIR_M1_R1], ordered, query_at="2026-09-22T11:00:00Z")
    )
    c = clocks[0]
    assert c["status"] == "active"
    assert c["worked_hours"] == 3
    assert c["deadline"] == "2026-09-23T10:00:00Z"


def test_naive_datetimes_treated_as_utc():
    body = {
        "pairs": [dict(PAIR_M1_R1, budget_hours=1)],
        "events": [_ev("m1", "r1", "accept", "2026-09-21T09:00:00")],
        "holidays": [],
        "query_at": "2026-09-21T10:00:00",
    }
    clocks = _clocks(body)
    assert clocks[0]["deadline"] == "2026-09-21T10:00:00Z"
    assert clocks[0]["overdue"] is False


# ---------- 排序与输出结构 ----------


def test_results_sorted_by_manuscript_then_reviewer():
    pairs = [
        {"manuscript_id": "m2", "reviewer_id": "r9", "budget_hours": 1},
        {"manuscript_id": "m1", "reviewer_id": "r2", "budget_hours": 1},
        {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 1},
    ]
    clocks = _clocks(
        _body(pairs, [], query_at="2026-09-21T18:00:00Z")
    )
    assert [(c["manuscript_id"], c["reviewer_id"]) for c in clocks] == [
        ("m1", "r1"), ("m1", "r2"), ("m2", "r9"),
    ]


def test_echo_query_at_and_holidays():
    status, data = _post(_body(
        [dict(PAIR_M1_R1, budget_hours=1)], [],
        holidays=["2026-09-22"], query_at="2026-09-21T18:00:00Z",
    ))
    assert status == 200
    assert data["query_at"] == "2026-09-21T18:00:00Z"
    assert data["holidays"] == ["2026-09-22"]


# ---------- 拒绝：重复配对 / 未知配对 / 同对同刻 / 非法转换 ----------


def test_duplicate_pair_rejected():
    body = _body([
        {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 2},
        {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 4},
    ], [])
    status, data = _post(body)
    assert status == 422
    errors = data["errors"]
    assert [e["type"] for e in errors] == ["duplicate_pair"]
    e = errors[0]
    assert e["manuscript_id"] == "m1" and e["reviewer_id"] == "r1"
    assert e["event_type"] is None and e["at"] is None and e["detail"]


def test_unknown_pair_event_rejected():
    body = _body(
        [PAIR_M1_R1],
        [_ev("m2", "r2", "accept", "2026-09-21T10:00:00Z")],
    )
    status, data = _post(body)
    assert status == 422
    errors = data["errors"]
    assert [e["type"] for e in errors] == ["unknown_pair"]
    assert errors[0]["event_type"] == "accept"
    assert errors[0]["at"] == "2026-09-21T10:00:00Z"


def test_same_pair_same_instant_events_rejected():
    # 同刻两条（类型不同）事件：拒绝第二条；两条类型相同同样拒绝
    for types in [("accept", "pause"), ("accept", "accept")]:
        body = _body([PAIR_M1_R1], [
            _ev("m1", "r1", types[0], "2026-09-21T10:00:00Z"),
            _ev("m1", "r1", types[1], "2026-09-21T10:00:00Z"),
            _ev("m1", "r1", "submit", "2026-09-22T10:00:00Z"),
        ])
        status, data = _post(body)
        assert status == 422, types
        assert [e["type"] for e in data["errors"]] == ["duplicate_event"]


def test_illegal_transitions_rejected():
    cases = [
        ("pending", "pause"),
        ("pending", "resume"),
        ("pending", "submit"),
    ]
    for state, etype in cases:
        body = _body([PAIR_M1_R1], [
            _ev("m1", "r1", etype, "2026-09-21T10:00:00Z"),
        ])
        status, data = _post(body)
        assert status == 422, (state, etype)
        assert data["errors"][0]["type"] == "invalid_transition"

    # active 不可重复 accept；paused 不可 pause/submit；submitted 后任何事件都非法
    active_bad = [
        _ev("m1", "r1", "accept", "2026-09-21T09:00:00Z"),
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
    ]
    status, data = _post(_body([PAIR_M1_R1], active_bad))
    assert status == 422 and data["errors"][0]["type"] == "invalid_transition"

    paused_bad = [
        _ev("m1", "r1", "accept", "2026-09-21T09:00:00Z"),
        _ev("m1", "r1", "pause", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "submit", "2026-09-21T11:00:00Z"),
    ]
    status, data = _post(_body([PAIR_M1_R1], paused_bad))
    assert status == 422
    assert [e["type"] for e in data["errors"]] == ["invalid_transition"]

    submitted_bad = [
        _ev("m1", "r1", "accept", "2026-09-21T09:00:00Z"),
        _ev("m1", "r1", "submit", "2026-09-21T10:00:00Z"),
        _ev("m1", "r1", "resume", "2026-09-21T11:00:00Z"),
    ]
    status, data = _post(_body([PAIR_M1_R1], submitted_bad))
    assert status == 422
    assert [e["type"] for e in data["errors"]] == ["invalid_transition"]


def test_illegal_event_skipped_so_later_events_still_replayed():
    # pending 下的 pause 非法被跳过；随后的 accept 仍可正常进入 active
    body = _body([PAIR_M1_R1], [
        _ev("m1", "r1", "pause", "2026-09-21T09:30:00Z"),
        _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
    ], query_at="2026-09-21T11:00:00Z")
    status, data = _post(body)
    assert status == 422
    assert [e["type"] for e in data["errors"]] == ["invalid_transition"]


def test_all_issues_collected_and_deterministically_sorted():
    body = _body(
        [
            {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 8},
            {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 8},
            {"manuscript_id": "m2", "reviewer_id": "r2", "budget_hours": 8},
        ],
        [
            _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
            _ev("m1", "r1", "accept", "2026-09-21T10:00:00Z"),
            _ev("m2", "r2", "pause", "2026-09-21T10:00:00Z"),
            _ev("x9", "r9", "accept", "2026-09-21T10:00:00Z"),
        ],
    )
    status, data_a = _post(body)
    assert status == 422
    # 输入列表重排（事件顺序打乱）不改变问题集合与顺序
    body["events"] = list(reversed(body["events"]))
    _, data_b = _post(body)
    assert data_a == data_b
    types = [e["type"] for e in data_a["errors"]]
    assert types == [
        "duplicate_event", "duplicate_pair",
        "invalid_transition", "unknown_pair",
    ]


# ---------- 请求级校验（FastAPI/Pydantic 422） ----------


def test_schema_level_validation():
    # budget_hours 必须为正整数
    status, _ = _post({"pairs": [
        {"manuscript_id": "m", "reviewer_id": "r", "budget_hours": 0}],
        "query_at": Q})
    assert status == 422
    # pairs 不能为空
    status, _ = _post({"pairs": [], "query_at": Q})
    assert status == 422
    # 缺少 query_at
    status, _ = _post({"pairs": [
        {"manuscript_id": "m", "reviewer_id": "r", "budget_hours": 1}]})
    assert status == 422
    # 未知事件类型 / 多余字段
    status, _ = _post({"pairs": [
        {"manuscript_id": "m", "reviewer_id": "r", "budget_hours": 1}],
        "events": [{"manuscript_id": "m", "reviewer_id": "r",
                    "type": "cancel", "at": Q}], "query_at": Q})
    assert status == 422
    status, _ = _post({"pairs": [
        {"manuscript_id": "m", "reviewer_id": "r", "budget_hours": 1,
         "note": "x"}], "query_at": Q})
    assert status == 422
