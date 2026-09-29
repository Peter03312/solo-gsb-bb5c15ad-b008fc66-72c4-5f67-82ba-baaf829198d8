"""POST /locate-quotes 的端到端测试。"""

from __future__ import annotations

import itertools
import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PATH = "/locate-quotes"


def _post(body: dict) -> tuple[int, dict]:
    resp = client.post(PATH, json=body)
    return resp.status_code, resp.json()


def _locate(text: str, quotes: list[tuple[str, int]], manuscript_id: str = "m1"):
    status, data = _post(
        {
            "manuscript_id": manuscript_id,
            "text": text,
            "quotes": [{"text": t, "hint_start": h} for t, h in quotes],
        }
    )
    assert status == 200, data
    assert data["status"] == "feasible", data
    assert data["manuscript_id"] == manuscript_id
    return [(item["start"], item["end"]) for item in data["locations"]], data[
        "total_deviation"
    ]


def _infeasible(text: str, quotes: list[tuple[str, int]]):
    status, data = _post(
        {
            "manuscript_id": "m1",
            "text": text,
            "quotes": [{"text": t, "hint_start": h} for t, h in quotes],
        }
    )
    assert status == 200, data
    assert data == {
        "status": "infeasible",
        "manuscript_id": "m1",
        "locations": None,
        "total_deviation": None,
    }


# ---------- 基础定位与总偏差 ----------


def test_single_quote_picks_occurrence_nearest_to_hint():
    # "ab" 出现在 0、6、9；提示 5 -> 最近 6（|6-5|=1）
    spans, dev = _locate("ab cd ab x ab", [("ab", 5)])
    assert spans == [(6, 8)]
    assert dev == 1


def test_hint_after_last_occurrence_picks_last():
    # 提示在最后一次出现之后但仍落在正文内（合法）：取最靠后的出现
    spans, dev = _locate("ab cd ab x ab", [("ab", 12)])
    assert spans == [(11, 13)]
    assert dev == 1


def test_spans_are_half_open_and_exact():
    text = "hello world"
    spans, dev = _locate(text, [("world", 6)])
    start, end = spans[0]
    assert text[start:end] == "world"
    assert (start, end) == (6, 11)
    assert dev == 0


def test_multiple_quotes_ordered_non_overlapping():
    text = "the cat and the dog and the fish"
    spans, dev = _locate(text, [("the", 20), ("and", 20), ("the", 20)])
    # the@12(偏差8) and@20(0) the@24(偏差4)：完整顺序方案
    assert spans == [(12, 15), (20, 23), (24, 27)]
    assert dev == 12
    assert spans[0][1] <= spans[1][0] <= spans[1][1] <= spans[2][0]


def test_adjacent_quotes_may_touch_but_not_overlap():
    text = "abcabc"
    spans, _ = _locate(text, [("abc", 0), ("abc", 0)])
    assert spans == [(0, 3), (3, 6)]


def test_overlapping_occurrences_are_both_considered():
    # "aa" 在 "aaa" 中出现在 0 和 1（彼此重叠）
    spans, dev = _locate("aaa", [("aa", 2)])
    assert spans == [(1, 3)]
    assert dev == 1


def test_hints_pull_away_from_trivial_order():
    # 提示把第一条引文拉向后面出现，第二条落位其后
    text = "x ... x ... x"
    # "x" @0,6,12
    spans, dev = _locate(text, [("x", 11), ("x", 12)])
    assert spans == [(6, 7), (12, 13)]
    assert dev == 5 + 0


# ---------- 字典序破同 ----------


def test_equal_deviation_single_quote_prefers_smaller_start():
    # "a" @0,3；提示 1 时两侧等距（均偏差 1）-> 取起点更小者
    spans, dev = _locate("a..a", [("a", 1)])
    assert spans == [(0, 1)]
    assert dev == 1


def test_lex_tiebreak_compares_full_start_sequence():
    # 两条相同引文 "a" 出现于 0、5、10；提示 (10,10)。
    # 完整方案如 (5,10)：偏差 5+0=5；(0,10)：10+0=10。
    # 关键同分对照：构造两组总偏差相同但首起点不同的方案。
    text = "a....a....a"  # a@0,5,10
    # hints (5,10): 方案 (5,10) 偏差 0+0=0 唯一最优
    spans, dev = _locate(text, [("a", 5), ("a", 10)])
    assert spans == [(5, 6), (10, 11)]
    assert dev == 0
    # hints (10,10): (5,10) 偏差 5；(0,5) 偏差 15；故 (5,10)
    spans, _ = _locate(text, [("a", 10), ("a", 10)])
    assert spans == [(5, 6), (10, 11)]


def test_lex_tiebreak_earlier_position_decides_when_later_tied():
    # 在两条引文上构造：多个完整方案总偏差相同，第一位置可区分
    # text: b a . a . a  -> "a"@1,3,5；两条引文
    # 完整方案 (1,3),(1,5),(3,5)。提示 (3,5)：
    #   (1,5): |1-3|+|5-5|=2；(3,5): 0+0=0；(1,3): 2+2=4 -> (3,5)
    text = "ba.a.a"
    spans, dev = _locate(text, [("a", 3), ("a", 5)])
    assert spans == [(3, 4), (5, 6)]
    assert dev == 0
    # 提示 (3,3)：(1,3)=2+0=2；(3,5)=0+2=2；同分 -> 字典序 (1,3)<(3,5)
    spans, dev = _locate(text, [("a", 3), ("a", 3)])
    assert spans == [(1, 2), (3, 4)]
    assert dev == 2


# ---------- 不可行：不泄露部分定位 ----------


def test_quote_with_any_occurrence_is_infeasible_not_partial():
    _infeasible("abcdef", [("abc", 0), ("zzz", 0)])


def test_overlap_forced_duplicate_long_quote_infeasible():
    # "abc" 只出现一次，两条都需要它且不重叠 -> 不可行
    _infeasible("abc", [("abc", 0), ("abc", 0)])


def test_ordering_cannot_cross_infeasible():
    # 同一单字符出现两次但提示强迫逆序也不影响：只要存在某完整顺序方案即可
    text = "a.a"
    # 两条 "a" 必须 (0,2)；可行
    spans, _ = _locate(text, [("a", 2), ("a", 0)])
    assert spans == [(0, 1), (2, 3)]
    # 三条 "a" 但只有两处出现 -> 不可行
    _infeasible(text, [("a", 0), ("a", 0), ("a", 0)])


def test_empty_text_with_nonempty_quote_is_infeasible():
    # 提示无法对空正文合法（任何非负提示都越界）；
    # 但模型层不可能给出 < 文本长度的提示，故经 422 路径。这里直接验证
    # 空正文 + 空引文组仍是可行空方案：
    status, data = _post({"manuscript_id": "m", "text": "", "quotes": []})
    assert status == 200
    assert data == {
        "status": "feasible",
        "manuscript_id": "m",
        "locations": [],
        "total_deviation": 0,
    }


# ---------- 非法输入：整体 422，不输出任何定位 ----------


def _errors(body: dict) -> list[dict]:
    status, data = _post(body)
    assert status == 422, data
    assert "locations" not in data and "status" not in data
    return data["errors"]


def test_empty_quote_rejected_structured():
    errors = _errors(
        {"manuscript_id": "m", "text": "abc", "quotes": [{"text": "", "hint_start": 0}]}
    )
    assert [e["type"] for e in errors] == ["empty_quote"]
    assert errors[0]["quote_index"] == 0
    assert errors[0]["hint"] is None


def test_hint_out_of_range_high_rejected():
    errors = _errors(
        {"manuscript_id": "m", "text": "abc", "quotes": [{"text": "a", "hint_start": 3}]}
    )
    assert errors[0]["type"] == "hint_out_of_range"
    assert errors[0]["quote_index"] == 0
    assert errors[0]["hint"] == 3


def test_negative_hint_rejected():
    errors = _errors(
        {"manuscript_id": "m", "text": "abc", "quotes": [{"text": "a", "hint_start": -1}]}
    )
    assert errors[0]["type"] == "hint_out_of_range"
    assert errors[0]["hint"] == -1


def test_empty_text_any_hint_is_out_of_range():
    errors = _errors(
        {"manuscript_id": "m", "text": "", "quotes": [{"text": "a", "hint_start": 0}]}
    )
    assert [e["type"] for e in errors] == ["hint_out_of_range"]


def test_empty_quote_and_bad_hint_both_reported_in_index_order():
    errors = _errors(
        {"manuscript_id": "m", "text": "", "quotes": [{"text": "", "hint_start": 7}]}
    )
    assert [e["type"] for e in errors] == ["empty_quote", "hint_out_of_range"]


def test_all_illegal_quotes_collected_together():
    errors = _errors(
        {
            "manuscript_id": "m",
            "text": "ab",
            "quotes": [
                {"text": "", "hint_start": 0},   # empty only (hint valid)
                {"text": "a", "hint_start": 5},  # range only
                {"text": "", "hint_start": -2},  # both
            ],
        }
    )
    # 按 (quote_index, 固定类型顺序)：i0 empty, i1 range, i2 empty, i2 range
    assert [(e["quote_index"], e["type"]) for e in errors] == [
        (0, "empty_quote"),
        (1, "hint_out_of_range"),
        (2, "empty_quote"),
        (2, "hint_out_of_range"),
    ]


def test_valid_quote_alongside_illegal_one_still_422():
    errors = _errors(
        {
            "manuscript_id": "m",
            "text": "abc",
            "quotes": [
                {"text": "a", "hint_start": 0},
                {"text": "b", "hint_start": 99},
            ],
        }
    )
    assert [(e["quote_index"], e["type"]) for e in errors] == [
        (1, "hint_out_of_range")
    ]


def test_missing_field_is_422():
    status, _ = _post({"manuscript_id": "m", "text": "abc"})
    assert status == 422


def test_extra_field_is_422():
    status, _ = _post(
        {
            "manuscript_id": "m",
            "text": "abc",
            "quotes": [{"text": "a", "hint_start": 0, "extra": 1}],
        }
    )
    assert status == 422


def test_manuscript_id_echoed_and_not_validated_against_registry():
    # 无状态：不查任何稿件库，任意非空 ID 原样回显
    spans, _ = _locate("abc", [("b", 1)], manuscript_id="any-id-123")
    assert spans == [(1, 2)]


# ---------- Unicode 码位语义 ----------


def test_unicode_astral_plane_counts_as_one_code_point():
    # 𝕏 (U+1F54F) 占一个 Python 码位
    text = "a𝕏b𝕏c"
    assert len(text) == 5
    spans, dev = _locate(text, [("𝕏", 3)])
    assert spans == [(3, 4)]
    assert dev == 0


def test_case_sensitive_exact_match():
    _infeasible("abc", [("ABC", 0)])
    spans, _ = _locate("abcABC", [("ABC", 3)])
    assert spans == [(3, 6)]


# ---------- 与暴力枚举 oracle 对拍 ----------


def _brute(text: str, quotes: list[tuple[str, int]]):
    occs = []
    for qtext, _ in quotes:
        positions, i = [], 0
        while True:
            j = text.find(qtext, i)
            if j < 0:
                break
            positions.append(j)
            i = j + 1
        if not positions:
            return None
        occs.append(positions)
    lens = [len(qtext) for qtext, _ in quotes]
    best = None
    for combo in itertools.product(*occs):
        if all(combo[k] + lens[k] <= combo[k + 1] for k in range(len(combo) - 1)):
            cost = sum(abs(combo[k] - quotes[k][1]) for k in range(len(combo)))
            key = (cost, list(combo))
            if best is None or key < best:
                best = key
    return best


def test_matches_brute_force_oracle_on_random_small_inputs():
    rng = random.Random(20260929)
    alphabet = "ab"
    for _ in range(1500):
        n = rng.randint(0, 9)
        text = "".join(rng.choice(alphabet) for _ in range(n))
        quotes: list[tuple[str, int]] = []
        for _ in range(rng.randint(0, 4)):
            if n == 0 or rng.random() < 0.35:
                qtext = "".join(
                    rng.choice(alphabet) for _ in range(rng.randint(1, 3))
                )
            else:
                lo = rng.randint(0, n - 1)
                hi = rng.randint(lo + 1, min(n, lo + 3))
                qtext = text[lo:hi]
            quotes.append((qtext, rng.randint(0, max(n - 1, 0))))

        expected = _brute(text, quotes)
        body = {
            "manuscript_id": "m",
            "text": text,
            "quotes": [{"text": t, "hint_start": h} for t, h in quotes],
        }
        status, data = _post(body)
        # 空正文 + 非空引文：提示 0 越界，整体 422（空引文组才走 200 空方案）
        if n == 0 and quotes:
            assert status == 422, (text, quotes, data)
            assert all(e["type"] == "hint_out_of_range" for e in data["errors"])
            continue
        assert status == 200, (text, quotes, data)
        if expected is None:
            assert data["status"] == "infeasible"
            assert data["locations"] is None
            assert data["total_deviation"] is None
        else:
            assert data["status"] == "feasible", (text, quotes, data)
            starts = [item["start"] for item in data["locations"]]
            assert (starts, data["total_deviation"]) == (
                expected[1],
                expected[0],
            ), (text, quotes, expected)
            # 区间逐字一致、有序且不重叠
            for k, item in enumerate(data["locations"]):
                assert text[item["start"]:item["end"]] == quotes[k][0]
                if k:
                    assert data["locations"][k - 1]["end"] <= item["start"]
                assert item["index"] == k


def test_duplicate_quote_texts_share_occurrences_and_still_ordered():
    text = "aaaa"  # "aa"@0,1,2
    spans, dev = _locate(text, [("aa", 0), ("aa", 0)])
    # 完整方案只有 (0,2)；(1,..) 后接不下第二条（需要起点 >=3，最大为 2）
    assert spans == [(0, 2), (2, 4)]
    assert dev == 2
