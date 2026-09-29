"""POST /locate-quotes 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post(body: dict) -> tuple[int, dict]:
    resp = client.post("/locate-quotes", json=body)
    return resp.status_code, resp.json()


def _place(text: str, quotes: list[tuple[str, int]], manuscript_id: str = "m1"):
    status, data = _post({
        "manuscript_id": manuscript_id,
        "text": text,
        "quotes": [
            {"text": q, "hint_start": h} for q, h in quotes
        ],
    })
    assert status == 200, data
    assert data["status"] == "feasible", data
    assert data["manuscript_id"] == manuscript_id
    return data["placements"], data["total_deviation"]


def _check_intervals(text: str, placements: list[dict], quotes: list[tuple[str, int]]):
    """通用不变量：区间逐字符等于引文、顺序不重叠、偏差与总和一致。"""
    last_end = 0
    total = 0
    for i, (p, (q, h)) in enumerate(zip(placements, quotes)):
        assert p["index"] == i
        assert text[p["start"]:p["end"]] == q
        assert p["end"] - p["start"] == len(q)
        assert p["start"] >= last_end, "位置必须按引文顺序且互不重叠"
        last_end = p["end"]
        assert p["deviation"] == abs(p["start"] - h)
        total += p["deviation"]
    return total


# ---------- 可行：完全一致匹配与偏差 ----------


def test_single_quote_exact_at_hint():
    text = "hello world"
    placements, total = _place(text, [("world", 6)])
    assert placements == [
        {"index": 0, "start": 6, "end": 11, "deviation": 0}
    ]
    assert total == 0


def test_single_quote_picks_nearest_of_repeated_occurrences():
    # "aba" 在 0 与 2 出现；提示 3 -> 取 2（偏差 1），不是 0（偏差 2）
    text = "ababa"
    placements, total = _place(text, [("aba", 3)])
    assert placements[0]["start"] == 2
    assert total == 1


def test_multiple_quotes_ordered_non_overlapping():
    # "aba" 出现于 0、2；"ba" 出现于 1、3。
    # q1=0 时 q2 须 >=3，取 "ba"@3；q1=2 时 q2 须 >=4，无出现 -> 唯一完整方案
    text = "ababa"
    quotes = [("aba", 0), ("ba", 4)]
    placements, total = _place(text, quotes)
    assert [(p["start"], p["end"]) for p in placements] == [(0, 3), (3, 5)]
    assert total == 1
    assert _check_intervals(text, placements, quotes) == total


def test_deviation_is_globally_minimized_not_independently_greedy():
    # 五条引文 "a"，提示都指向 2：全局最优是连续占位使到 2 的距离和最小
    text = "aaaaa"
    quotes = [("a", 2)] * 5
    placements, total = _place(text, quotes)
    assert [p["start"] for p in placements] == [0, 1, 2, 3, 4]
    assert total == 6  # 2+1+0+1+2
    assert _check_intervals(text, placements, quotes) == total


def test_tie_broken_by_lexicographically_smallest_start_sequence():
    # "aaa" 中两条 "a"，提示均为 1：
    # (0,1) 偏差 1+0=1 与 (1,2) 偏差 0+1=1 同分 -> 取起点序列字典序最小的 (0,1)
    text = "aaa"
    quotes = [("a", 1), ("a", 1)]
    placements, total = _place(text, quotes)
    assert [p["start"] for p in placements] == [0, 1]
    assert total == 1


def test_unicode_positions_are_code_points():
    # 位置按 Unicode 码点（字符）计，而非 UTF-8 / UTF-16 编码单元
    text = "你好世界你好"
    placements, total = _place(text, [("你好", 4)])
    assert [(p["start"], p["end"]) for p in placements] == [(4, 6)]
    assert total == 0
    # 含代理对（emoji）同样按字符计
    text2 = "a😀b😀c"
    placements2, _ = _place(text2, [("😀", 3)])
    assert [(p["start"], p["end"]) for p in placements2] == [(3, 4)]


def test_hint_at_end_boundary_is_legal():
    # hint_start == len(text) 是合法的文末边界（偏差照常计）
    text = "abc"
    placements, total = _place(text, [("abc", 3)])
    assert placements[0]["start"] == 0
    assert total == 3


def test_empty_quote_list_is_empty_solution():
    status, data = _post({
        "manuscript_id": "m1", "text": "abc", "quotes": [],
    })
    assert status == 200
    assert data == {
        "manuscript_id": "m1",
        "status": "feasible",
        "placements": [],
        "total_deviation": 0,
    }


def test_deterministic_across_calls():
    body = {
        "manuscript_id": "m1",
        "text": "the cat sat on the mat with the hat",
        "quotes": [
            {"text": "the", "hint_start": 20},
            {"text": "the", "hint_start": 1},
            {"text": "cat", "hint_start": 4},
        ],
    }
    _, first = _post(body)
    for _ in range(3):
        _, again = _post(body)
        assert again == first


# ---------- 不可行：不输出任何部分定位 ----------


def test_quote_without_exact_occurrence_is_infeasible():
    # 逐字符完全一致才计入出现；缺失任一条即整体不可行，不输出部分定位
    status, data = _post({
        "manuscript_id": "m1",
        "text": "abc",
        "quotes": [
            {"text": "a", "hint_start": 0},
            {"text": "xyz", "hint_start": 0},
        ],
    })
    assert status == 200
    assert data == {
        "manuscript_id": "m1",
        "status": "infeasible",
        "placements": None,
        "total_deviation": None,
    }


def test_overlapping_only_solutions_are_infeasible_without_partial():
    # "aaa" 中两条 "aa"：出现于 0、1，任意两条都重叠 -> 无完整方案
    status, data = _post({
        "manuscript_id": "m1",
        "text": "aaa",
        "quotes": [
            {"text": "aa", "hint_start": 0},
            {"text": "aa", "hint_start": 0},
        ],
    })
    assert status == 200
    assert data["status"] == "infeasible"
    # 不泄露任何部分定位
    assert data["placements"] is None
    assert data["total_deviation"] is None


def test_empty_text_with_nonempty_quote_is_infeasible():
    status, data = _post({
        "manuscript_id": "m1",
        "text": "",
        "quotes": [{"text": "a", "hint_start": 0}],
    })
    assert status == 200
    assert data["status"] == "infeasible"
    assert data["placements"] is None


# ---------- 非法输入：整体 422，结构化 errors ----------


def test_empty_quote_text_rejected_422():
    status, data = _post({
        "manuscript_id": "m1",
        "text": "abc",
        "quotes": [{"text": "", "hint_start": 0}],
    })
    assert status == 422
    assert data == {
        "errors": [
            {
                "type": "empty_quote",
                "index": 0,
                "detail": "第 0 条引文的 text 为空字符串，引文必须是非空原文片段",
            }
        ]
    }


def test_hint_out_of_bounds_rejected_422():
    status, data = _post({
        "manuscript_id": "m1",
        "text": "abc",
        "quotes": [
            {"text": "a", "hint_start": -1},
            {"text": "b", "hint_start": 4},  # len(text)=3，合法范围 0..3
        ],
    })
    assert status == 422
    assert [e["type"] for e in data["errors"]] == [
        "hint_out_of_bounds", "hint_out_of_bounds"
    ]
    assert [e["index"] for e in data["errors"]] == [0, 1]
    for e in data["errors"]:
        assert "越界" in e["detail"]


def test_illegal_inputs_collected_and_sorted():
    # 同一引文可同时是空文本与越界提示；问题按 (type, index) 排序，
    # empty_quote 字典序先于 hint_out_of_bounds
    status, data = _post({
        "manuscript_id": "m1",
        "text": "abc",
        "quotes": [
            {"text": "", "hint_start": 9},    # index 0: empty + oob
            {"text": "a", "hint_start": 9},   # index 1: oob
        ],
    })
    assert status == 422
    assert [(e["type"], e["index"]) for e in data["errors"]] == [
        ("empty_quote", 0),
        ("hint_out_of_bounds", 0),
        ("hint_out_of_bounds", 1),
    ]


def test_unknown_field_rejected():
    status, _ = _post({
        "manuscript_id": "m1",
        "text": "abc",
        "quotes": [],
        "unexpected": 1,
    })
    assert status == 422


def test_overlong_inputs_rejected():
    # 正文 / 引文长度与引文条数受上限约束
    status, _ = _post({
        "manuscript_id": "m1",
        "text": "a" * 50_001,
        "quotes": [],
    })
    assert status == 422

    status, _ = _post({
        "manuscript_id": "m1",
        "text": "a",
        "quotes": [{"text": "a", "hint_start": 0}] * 101,
    })
    assert status == 422
