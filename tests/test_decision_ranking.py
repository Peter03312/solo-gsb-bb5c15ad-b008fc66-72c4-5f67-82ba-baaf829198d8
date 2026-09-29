"""POST /decision-ranking 的端到端测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _post(body: dict) -> tuple[int, dict]:
    resp = client.post("/decision-ranking", json=body)
    return resp.status_code, resp.json()


def _rank(body: dict) -> list[str]:
    status, data = _post(body)
    assert status == 200, data
    return data["ranking"]


def _support(data: dict) -> dict[tuple[str, str], int]:
    return {
        (p["manuscript_id_a"], p["manuscript_id_b"]): p["support"]
        for p in data["pair_support"]
    }


def _assign(pairs: dict[str, list[str]]) -> list[dict]:
    return [
        {"manuscript_id": mid, "reviewer_ids": rids}
        for mid, rids in pairs.items()
    ]


def _ballot(rid: str, ranking: list[str]) -> dict:
    return {"reviewer_id": rid, "ranking": ranking}


# ---------- 基本多数与 ID 破同 ----------


def test_simple_majority_order():
    body = {
        "assignments": _assign({"m1": ["r1", "r2", "r3"], "m2": ["r1", "r2", "r3"]}),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r2", ["m1", "m2"]),
            _ballot("r3", ["m2", "m1"]),
        ],
    }
    assert _rank(body) == ["m1", "m2"]


def test_no_ballots_breaks_tie_by_manuscript_id():
    # 无任何票：全部路径强度相等，按稿件 ID 升序
    body = {
        "assignments": _assign({"m9": ["r1"], "m2": ["r1"], "m5": ["r1"]}),
        "ballots": [],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m2", "m5", "m9"]


def test_empty_ranking_is_a_valid_noop_ballot():
    body = {
        "assignments": _assign({"m1": ["r1", "r2"], "m2": ["r1", "r2"]}),
        "ballots": [
            _ballot("r1", []),  # 空票等于不投票
            _ballot("r2", ["m2", "m1"]),
        ],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m2", "m1"]


def test_manuscript_without_reviewers_is_still_ranked():
    # m1 无人可评（无票可包含它）；r1 仅排 m2 一篇，不构成任何成对比较，
    # 故 m1/m2 路径强度相等，按 ID 破同：无评审人的 m1 仍出现在完整顺序中
    body = {
        "assignments": _assign({"m1": [], "m2": ["r1"]}),
        "ballots": [_ballot("r1", ["m2"])],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m1", "m2"]
    s = _support(data)
    assert s[("m2", "m1")] == 0 and s[("m1", "m2")] == 0


# ---------- 部分票：未同票出现的两稿不计成对支持 ----------


def test_partial_rankings_only_count_co_ranked_pairs():
    # r1: m1>m2（m3 未排）；r3: m3>m1（m2 未排）；r2 仅排 m2（不成对）
    body = {
        "assignments": _assign({
            "m1": ["r1", "r2", "r3"],
            "m2": ["r1", "r2", "r3"],
            "m3": ["r1", "r2", "r3"],
        }),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r2", ["m2"]),
            _ballot("r3", ["m3", "m1"]),
        ],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m3", "m1", "m2"]
    s = _support(data)
    assert s[("m1", "m2")] == 1 and s[("m2", "m1")] == 0
    assert s[("m3", "m1")] == 1 and s[("m1", "m3")] == 0
    # m2 与 m3 从未在同一张票中同时出现：双向支持均为 0
    assert s[("m2", "m3")] == 0 and s[("m3", "m2")] == 0


# ---------- Schulze 最强路径 ----------


def test_strongest_path_resolves_cycle_differently_from_direct_pairwise():
    # 8 名评审人，每人分到 m1/m2/m3；成对边际（严格 5:3）：
    #   d[m1][m2]=5, d[m2][m1]=3
    #   d[m2][m3]=5, d[m3][m2]=3
    #   d[m3][m1]=5, d[m1][m3]=3
    # 票型：2 x [m3,m1,m2]、2 x [m1,m2,m3]、3 x [m2,m3,m1]、1 x [m1,m3,m2]
    profile = (
        [["m3", "m1", "m2"]] * 2
        + [["m1", "m2", "m3"]] * 2
        + [["m2", "m3", "m1"]] * 3
        + [["m1", "m3", "m2"]] * 1
    )
    reviewers = [f"r{i}" for i in range(8)]
    body = {
        "assignments": _assign({m: reviewers for m in ("m1", "m2", "m3")}),
        "ballots": [_ballot(r, p) for r, p in zip(reviewers, profile)],
    }
    status, data = _post(body)
    assert status == 200, data
    # 直接成对胜者成环（m3>m1>m2>m3）；最强路径使三方路径强度皆为 5 而
    # 互相持平，按 ID 破同得 m1,m2,m3
    assert data["ranking"] == ["m1", "m2", "m3"]
    s = _support(data)
    assert s[("m1", "m2")] == 5 and s[("m2", "m1")] == 3
    assert s[("m2", "m3")] == 5 and s[("m3", "m2")] == 3
    assert s[("m3", "m1")] == 5 and s[("m1", "m3")] == 3


def test_strongest_path_transitive_defeat():
    # d: A>B=2/0, B>C=2/0, C>A=0/2 之外构造 A 通过 B 击败 C 的链式优势：
    # r1: m1>m2>m3；r2: m1>m2>m3；r3: m2>m3（m1 未排）
    # d[m1][m2]=2, d[m2][m3]=3, d[m1][m3]=2；路径 m1->m2->m3 强度 min(2,3)=2
    body = {
        "assignments": _assign({
            "m1": ["r1", "r2"], "m2": ["r1", "r2", "r3"], "m3": ["r1", "r2", "r3"],
        }),
        "ballots": [
            _ballot("r1", ["m1", "m2", "m3"]),
            _ballot("r2", ["m1", "m2", "m3"]),
            _ballot("r3", ["m2", "m3"]),
        ],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m1", "m2", "m3"]


def test_partial_ballots_use_topological_order_with_id_tiebreak():
    # 部分票会让「无差异」关系不传递（m3/m4 与链上各稿路径强度持平，但链本身
    # m1>m6>m5>m2 有严格强弱）。逐对比较器 + 通用排序会因比较器环把 m2 错排到
    # m6 之前；正确结果是在严格偏好 DAG 上每步取最小 ID 零入度稿件：
    # 初始零入度 {m1,m3,m4} -> m1,m3,m4；m1 出后释放 m6 -> m6；再 m5 -> m2。
    body = {
        "assignments": _assign({
            "m1": ["r1"], "m2": ["r1"], "m3": ["r1"],
            "m4": ["r1"], "m5": ["r1"], "m6": ["r1"],
        }),
        "ballots": [_ballot("r1", ["m1", "m6", "m5", "m2"])],
    }
    status, data = _post(body)
    assert status == 200
    assert data["ranking"] == ["m1", "m3", "m4", "m6", "m5", "m2"]
    pos = {m: i for i, m in enumerate(data["ranking"])}
    # 链上的全部严格强于关系都必须保持
    for earlier, later in [
        ("m1", "m6"), ("m1", "m5"), ("m1", "m2"),
        ("m6", "m5"), ("m6", "m2"), ("m5", "m2"),
    ]:
        assert pos[earlier] < pos[later]
    # 无严格关系的 m3/m4 按 ID 升序且在被阻塞链节点之前就绪
    assert pos["m3"] < pos["m4"]


def test_path_strength_is_minimum_edge_along_path():
    # 边权：m1->m2=4，m2->m3=2（瓶颈），另有 m1->m3 直接为 1（成对胜者但弱）。
    # r1..r4: m1>m2；r5,r6: m2>m3（共需 m1->m3 支持 1）：
    #   4 票 [m1,m2,m3] + 2 票 [m2,m3] + 1 票 [m3,m1]（m3>m1 使 m1->m3=4 非1…）
    # 直接构造：d[m1][m2]=4/0；d[m2][m3]=6/0；d[m1][m3]=4/2（4 张含 m1>m3，
    # 2 张 m3>m1 且这 2 张不含 m2 与 m1 的同票比较之外）
    ballots = (
        [_ballot(f"r{i}", ["m1", "m2", "m3"]) for i in range(1, 5)]  # 4 票
        + [_ballot(f"r{i}", ["m2", "m3"]) for i in range(5, 7)]      # 2 票
        + [_ballot("r7", ["m3", "m1"])]                              # 1 票 m3>m1
    )
    body = {
        "assignments": _assign({
            "m1": ["r1", "r2", "r3", "r4", "r7"],
            "m2": ["r1", "r2", "r3", "r4", "r5", "r6"],
            "m3": ["r1", "r2", "r3", "r4", "r5", "r6", "r7"],
        }),
        "ballots": ballots,
    }
    status, data = _post(body)
    assert status == 200
    # d[m1][m3]=4, d[m3][m1]=1；路径 m1->m2(4)->m3(6) 强度 4，与直接边持平。
    # 全部为传递序：m1 > m2 > m3
    assert data["ranking"] == ["m1", "m2", "m3"]
    s = _support(data)
    assert s[("m1", "m2")] == 4 and s[("m2", "m1")] == 0
    assert s[("m2", "m3")] == 6 and s[("m3", "m2")] == 0
    assert s[("m1", "m3")] == 4 and s[("m3", "m1")] == 1


def test_pairwise_tie_strength_falls_back_to_id():
    # m1/m2 双向直接支持相等（1:1）且无路径可打破：ID 破同
    body = {
        "assignments": _assign({"m1": ["r1", "r2"], "m2": ["r1", "r2"]}),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r2", ["m2", "m1"]),
        ],
    }
    assert _rank(body) == ["m1", "m2"]


# ---------- 输出结构 ----------


def test_pair_support_covers_all_ordered_pairs_sorted():
    body = {
        "assignments": _assign({"m1": ["r1"], "m2": ["r1"]}),
        "ballots": [_ballot("r1", ["m1", "m2"])],
    }
    status, data = _post(body)
    assert status == 200
    pairs = [
        (p["manuscript_id_a"], p["manuscript_id_b"], p["support"])
        for p in data["pair_support"]
    ]
    assert pairs == [("m1", "m2", 1), ("m2", "m1", 0)]
    # 不含对角
    assert all(p["manuscript_id_a"] != p["manuscript_id_b"] for p in data["pair_support"])


def test_ranking_covers_every_assigned_manuscript_once():
    body = {
        "assignments": _assign({"m1": ["r1"], "m2": ["r1"], "m3": ["r1"]}),
        "ballots": [_ballot("r1", ["m3"])],  # 只排一篇
    }
    status, data = _post(body)
    assert status == 200
    assert sorted(data["ranking"]) == ["m1", "m2", "m3"]
    assert len(data["ranking"]) == 3


# ---------- 确定性：输入重排不改变结果 ----------


def test_reordering_inputs_does_not_change_result():
    body_a = {
        "assignments": [
            {"manuscript_id": "m2", "reviewer_ids": ["r2", "r1"]},
            {"manuscript_id": "m1", "reviewer_ids": ["r1", "r1", "r2"]},  # 重复自动去重
        ],
        "ballots": [
            _ballot("r2", ["m2", "m1"]),
            _ballot("r1", ["m1", "m2"]),
        ],
    }
    body_b = {
        "assignments": [
            {"manuscript_id": "m1", "reviewer_ids": ["r2", "r1"]},
            {"manuscript_id": "m2", "reviewer_ids": ["r1", "r2"]},
        ],
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r2", ["m2", "m1"]),
        ],
    }
    status_a, data_a = _post(body_a)
    status_b, data_b = _post(body_b)
    assert status_a == status_b == 200
    assert data_a == data_b
    # 1:1 平局 -> ID 破同
    assert data_a["ranking"] == ["m1", "m2"]


# ---------- 非法投票：422 ----------


def test_duplicate_ballot_rejected():
    body = {
        "assignments": _assign({"m1": ["r1"], "m2": ["r1"]}),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r1", ["m2", "m1"]),
        ],
    }
    status, data = _post(body)
    assert status == 422
    assert [e["type"] for e in data["errors"]] == ["duplicate_ballot"]
    e = data["errors"][0]
    assert e["reviewer_id"] == "r1" and e["manuscript_id"] is None and e["detail"]


def test_duplicate_ballot_still_validated_for_other_issues():
    # 第二张 r1 票本身还越权（投了未分配给自己的 m2）：重复与越权一并报告，
    # 且交换两张票的顺序不改变问题集合（不把任意一张当作「权威票」跳过校验）
    ballots = [
        _ballot("r1", ["m1"]),
        _ballot("r1", ["m2"]),
    ]
    body = {"assignments": _assign({"m1": ["r1"], "m2": ["r2"]}), "ballots": ballots}
    status, data_a = _post(body)
    assert status == 422
    assert {(e["type"], e["manuscript_id"]) for e in data_a["errors"]} == {
        ("duplicate_ballot", None),
        ("manuscript_not_assigned", "m2"),
    }
    body["ballots"] = list(reversed(ballots))
    _, data_b = _post(body)
    assert data_a == data_b


def test_duplicate_manuscript_in_ballot_rejected():
    body = {
        "assignments": _assign({"m1": ["r1"], "m2": ["r1"]}),
        "ballots": [_ballot("r1", ["m1", "m2", "m1"])],
    }
    status, data = _post(body)
    assert status == 422
    assert data["errors"] == [{
        "type": "duplicate_manuscript",
        "reviewer_id": "r1",
        "manuscript_id": "m1",
        "detail": data["errors"][0]["detail"],
    }]
    assert data["errors"][0]["detail"]


def test_manuscript_not_assigned_rejected():
    body = {
        # r1 只分到 m1，却对 m2 投票；r9 不在任何名单中
        "assignments": _assign({"m1": ["r1"], "m2": ["r2"]}),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r9", ["m1"]),
        ],
    }
    status, data = _post(body)
    assert status == 422
    errors = data["errors"]
    assert {(e["type"], e["reviewer_id"], e["manuscript_id"]) for e in errors} == {
        ("manuscript_not_assigned", "r1", "m2"),
        ("manuscript_not_assigned", "r9", "m1"),
    }
    # 确定性排序：type -> reviewer_id -> manuscript_id
    assert errors == sorted(errors, key=lambda e: (e["type"], e["reviewer_id"], e["manuscript_id"]))


def test_all_issues_collected_and_sorted_deterministically():
    body = {
        "assignments": _assign({"m1": ["r1", "r2"], "m2": ["r1", "r2"]}),
        "ballots": [
            _ballot("r1", ["m1", "m2"]),
            _ballot("r1", ["m1"]),                       # duplicate_ballot
            _ballot("r2", ["m2", "m2"]),                 # 票内重复（r2 已被分配，仅 duplicate_manuscript）
            _ballot("r3", ["m1"]),                       # 不在分配名单
        ],
    }
    status, data_a = _post(body)
    assert status == 422
    body["ballots"] = list(reversed(body["ballots"]))
    _, data_b = _post(body)
    assert data_a == data_b  # 输入重排不改变问题集合与顺序
    types = [e["type"] for e in data_a["errors"]]
    assert types == [
        "duplicate_ballot",
        "duplicate_manuscript",
        "manuscript_not_assigned",
    ]


def test_duplicate_and_unassigned_issues_both_reported_for_one_ballot():
    # r3 未被分配任何稿件，其票中 m2 既重复又越权：两类问题一并收集
    body = {
        "assignments": _assign({"m1": ["r1"], "m2": ["r1"]}),
        "ballots": [_ballot("r3", ["m2", "m2"])],
    }
    status, data = _post(body)
    assert status == 422
    assert {(e["type"], e["manuscript_id"]) for e in data["errors"]} == {
        ("duplicate_manuscript", "m2"),
        ("manuscript_not_assigned", "m2"),
    }


def test_illegal_request_outputs_no_ranking_fields():
    body = {
        "assignments": _assign({"m1": ["r1"]}),
        "ballots": [_ballot("r1", ["m1"]), _ballot("r1", ["m1"])],
    }
    status, data = _post(body)
    assert status == 422
    assert set(data) == {"errors"}


# ---------- 请求级校验（FastAPI/Pydantic 422） ----------


def test_empty_assignments_returns_empty_order():
    # 零篇稿件的可行 /assign 名单：共识顺序与成对支持均为空（200）
    status, data = _post({"assignments": [], "ballots": []})
    assert status == 200
    assert data == {"ranking": [], "pair_support": []}


def test_schema_level_validation():
    # 缺少 assignments
    status, _ = _post({"ballots": []})
    assert status == 422
    # 空稿件 ID
    status, _ = _post({"assignments": [{"manuscript_id": "", "reviewer_ids": ["r1"]}]})
    assert status == 422
    # 多余字段
    status, _ = _post({
        "assignments": [{"manuscript_id": "m1", "reviewer_ids": ["r1"], "note": "x"}],
    })
    assert status == 422
    status, _ = _post({
        "assignments": _assign({"m1": ["r1"]}),
        "ballots": [{"reviewer_id": "r1", "ranking": ["m1"], "extra": 1}],
    })
    assert status == 422
    # 票内字段缺失
    status, _ = _post({
        "assignments": _assign({"m1": ["r1"]}),
        "ballots": [{"reviewer_id": "r1"}],
    })
    assert status == 422
    # 票内空稿件 ID
    status, _ = _post({
        "assignments": _assign({"m1": ["r1"]}),
        "ballots": [{"reviewer_id": "r1", "ranking": [""]}],
    })
    assert status == 422
