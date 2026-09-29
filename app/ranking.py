"""共识排序：Schulze 最强路径法（POST /decision-ranking）。

输入由 `/assign` 可行分配名单（稿件 -> 评审人）与评审人对其**已评稿件**的
严格排序票组成。每位评审人至多一票；票内稿件不得重复，且每一篇都必须出现在
其分配名单中。允许只排部分稿件：仅在同一张票内同时出现的两稿才计入成对支持，
一张票未排出的稿件对该票不表达任何偏好（既不支持甲在前，也不支持乙在前）。

直接支持 d[甲][乙] = 将甲排在乙之前的票数（两稿须同时出现在该票中）。
Schulze 最强路径按题面规则计算：

- 仅当 d[甲][乙] > d[乙][甲]（成对胜者）时，甲 -> 乙 才作为一条路径边，
  边权为 d[甲][乙]；其余（平局 / 成对负方）直接支持不得作为路径边；
- 路径强度 = 沿途各边边权的最小值；甲对乙的路径强度 p[甲][乙] 为
  甲到乙全部路径的路径强度最大值（最宽路径）；
- p[甲][乙] > p[乙][甲] 时甲必须排在乙前；无法区分（相等）时按稿件 ID 破同。

严格偏好（p[甲][乙] > p[乙][甲]）构成无环有向图（DAG）。需要注意部分票下
「无差异」关系可能不传递（甲 ~ 丙、丙 ~ 乙，但甲 ≻ 乙），因此不能把
「比较路径强度、相等再比 ID」直接当作通用排序的比较器（会形成比较器环）。
本实现在严格偏好 DAG 上取**按 ID 破同的拓扑序**（每步在零入度稿件中取 ID
最小者）：所有严格强于关系保持先后，无严格关系的稿件由 ID 升序确定，得到
覆盖全部稿件的完整、确定的共识顺序。

实现只依赖整数与固定的 ID 排序，相同业务输入（甚至不同的列表顺序）必然产生
完全相同的结果：成对支持只统计票集合，路径强度用 Floyd-Warshall 计算
（最宽路径值与松弛顺序无关），最终拓扑序的破同键为稿件 ID。
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class RankingBallot:
    """一位评审人的严格排序票：按最前 -> 最后排列的稿件 ID。"""

    reviewer_id: str
    ranking: tuple[str, ...]


@dataclass(frozen=True)
class RankingIssue:
    """一条非法投票原因（结构化，整体 422 时返回全部问题）。"""

    type: str  # duplicate_ballot / duplicate_manuscript / manuscript_not_assigned
    reviewer_id: str
    # 票内重复或不属于其分配名单时携带对应稿件 ID；duplicate_ballot 为 None
    manuscript_id: str | None
    detail: str


@dataclass(frozen=True)
class PairSupport:
    """一个有序稿件对的直接成对支持：将 manuscript_id_a 排在 manuscript_id_b 前的票数。"""

    manuscript_id_a: str
    manuscript_id_b: str
    support: int


@dataclass(frozen=True)
class RankingResult:
    issues: tuple[RankingIssue, ...]
    # 共识顺序（第一名 -> 最后一名）；有非法票时为空（其长度即稿件全集大小）
    order: tuple[str, ...]
    # 全部有序稿件对 (a != b) 的直接成对支持，按 (甲, 乙) ID 排序；有非法票时为空
    pair_support: tuple[PairSupport, ...]


def _issue_sort_key(issue: RankingIssue) -> tuple[str, str, str]:
    return (issue.type, issue.reviewer_id, issue.manuscript_id or "")


def schulze_rank(
    assignments: list[tuple[str, list[str]]],
    ballots: list[RankingBallot],
) -> RankingResult:
    """计算共识顺序。

    assignments 为 /assign 的可行分配名单（``(manuscript_id, reviewer_ids)``
    列表，条目与评审人重复自动去重，``reviewer_ids`` 可为空列表表示该稿无人
    可评——空名单稿件仍参与共识顺序）；ballots 为评审人严格排序票。存在任何
    非法票时 issues 非空（确定性排序），此时 order / pair_support 为空、调用方
    应整体拒绝（422），不输出任何排序结果。
    """
    # 分配名单去重：稿件 -> 被分到该稿的评审人集合
    assigned: dict[str, set[str]] = {}
    for mid, rids in assignments:
        assigned.setdefault(mid, set()).update(rids)
    # 评审人 -> 其被分到的稿件集合（票内合法性据此判定）
    reviewer_manuscripts: dict[str, set[str]] = {}
    for mid, rids in assigned.items():
        for rid in rids:
            reviewer_manuscripts.setdefault(rid, set()).add(mid)

    manuscript_ids = tuple(sorted(assigned))
    index = {mid: i for i, mid in enumerate(manuscript_ids)}
    n = len(manuscript_ids)

    issues: list[RankingIssue] = []

    # ---- 1. 票级校验：每人至多一票；票内稿件不得重复且必须属于其分配名单 ----
    # 每张票都做完整校验（不因是重复票而跳过内容校验）：第 2 张及以后的同评审人
    # 票各记一条 duplicate_ballot，所有票的问题都照常收集。问题集合只取决于票
    # 多重集与排序键，与输入顺序无关。
    seen_ballot: set[str] = set()
    for ballot in ballots:
        rid = ballot.reviewer_id
        if rid in seen_ballot:
            issues.append(RankingIssue(
                type="duplicate_ballot",
                reviewer_id=rid,
                manuscript_id=None,
                detail=f"评审人 {rid} 提交了多张排序票，每人最多一票",
            ))
        seen_ballot.add(rid)

        eligible = reviewer_manuscripts.get(rid, set())
        counts = Counter(ballot.ranking)
        # 票内重复稿件（各记一条，与输入顺序无关）
        for mid in sorted(m for m, c in counts.items() if c > 1):
            issues.append(RankingIssue(
                type="duplicate_manuscript",
                reviewer_id=rid,
                manuscript_id=mid,
                detail=f"评审人 {rid} 的票内稿件 {mid} 重复出现，票内稿件不得重复",
            ))
        # 稿件不属于其分配名单（去重后各记一条）
        for mid in sorted(set(ballot.ranking) - eligible):
            issues.append(RankingIssue(
                type="manuscript_not_assigned",
                reviewer_id=rid,
                manuscript_id=mid,
                detail=(
                    f"稿件 {mid} 不在评审人 {rid} 的可行分配名单中，"
                    f"评审人只能对分配给自己的稿件投票"
                ),
            ))

    if issues:
        issues.sort(key=_issue_sort_key)
        return RankingResult(
            issues=tuple(issues),
            order=(),
            pair_support=(),
        )

    # ---- 2. 直接成对支持：仅两稿同时出现在同票中时，靠前者得到一票 ----
    d = [[0] * n for _ in range(n)]
    for ballot in ballots:
        ranked = ballot.ranking  # 校验后票内无重复
        for x in range(len(ranked)):
            i = index[ranked[x]]
            for y in range(x + 1, len(ranked)):
                j = index[ranked[y]]
                d[i][j] += 1

    # ---- 3. Schulze 最强路径：仅成对胜者的直接支持可作为路径边 ----
    # p[i][j] 初始化为直接边权（d[i][j] > d[j][i] 时为 d[i][j]，否则为 0，
    # 即不存在该边）；Floyd-Warshall 求最宽路径：
    # p[i][j] = max_k min(p[i][k], p[k][j])。
    # 松弛遍历全部 (k, i, j) 三元组，不排除 i/j == k 或 j == i：Schulze 标准
    # 形式允许路径在中间节点处「转向」任意终点（含终点为起点的情形）；跳过
    # j == i 会漏掉合法最强路径（例如 p[1][0] 需经 1->6->5->0 这样的链式
    # 传递，少松弛一步即断链）。对角与自环恒为 0，纳入循环也不会改变结果。
    p = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j and d[i][j] > d[j][i]:
                p[i][j] = d[i][j]
    for k in range(n):
        for i in range(n):
            for j in range(n):
                through_k = p[i][k] if p[i][k] < p[k][j] else p[k][j]
                if through_k > p[i][j]:
                    p[i][j] = through_k

    # ---- 4. 共识全序：严格偏好 DAG 上按 ID 破同的确定性拓扑序 ----
    # 以「i 严格强于 j（p[i][j] > p[j][i]）」为有向边 i -> j。Schulze 最强路径
    # 的严格偏好关系无环（对任意 i、j，两个方向的严格大于至多其一），故其为
    # DAG；但**无差异**（p[i][j] == p[j][i]）关系在部分票下不传递——例如
    # i 与 k 无差异、k 与 j 无差异、而 i 严格强于 j。此时「强度比较 + ID 破同」
    # 的逐对比较器不是严格弱序，直接喂给通用排序会因比较器环而漏排。正确做法：
    # 在严格偏好 DAG 上取线性扩张（拓扑序），并在每一步的零入度候选中始终取
    # 稿件 ID 最小者（min-heap Kahn）——
    #   * 所有严格强于关系在序中保持（每条 i -> j 边都有 i 先于 j）；
    #   * 无严格关系的两稿在「谁先就绪谁先出」中由 ID 决定，ID 升序破同；
    #   * 结果只依赖边集与 ID，与输入顺序无关，完全确定。
    indegree = [0] * n
    successors: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j and p[i][j] > p[j][i]:
                successors[i].append(j)
                indegree[j] += 1

    heap: list[tuple[str, int]] = [
        (manuscript_ids[i], i) for i in range(n) if indegree[i] == 0
    ]
    heapq.heapify(heap)
    ordered: list[str] = []
    while heap:
        _mid, i = heapq.heappop(heap)
        ordered.append(manuscript_ids[i])
        for j in successors[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(heap, (manuscript_ids[j], j))

    # Schulze 严格偏好无环：全部稿件必须能被排出（DAG 拓扑序覆盖所有节点）
    assert len(ordered) == n, "Schulze 严格偏好关系出现环"
    order_tuple = tuple(ordered)

    pair_support = tuple(
        PairSupport(
            manuscript_id_a=manuscript_ids[i],
            manuscript_id_b=manuscript_ids[j],
            support=d[i][j],
        )
        for i in range(n)
        for j in range(n)
        if i != j
    )
    return RankingResult(
        issues=(),
        order=order_tuple,
        pair_support=pair_support,
    )
