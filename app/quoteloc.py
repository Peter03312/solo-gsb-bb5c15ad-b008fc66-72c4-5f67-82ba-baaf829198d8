"""评审引文定位（POST /locate-quotes）。

评审意见引用稿件原文时，把一组按正文顺序排列的引文定位到**同一篇稿件**：
为每条引文选择一处与引文文本**完全一致**（区分大小写、逐 Unicode 码位）的
出现，要求所选区间按引文顺序排列且互不重叠（前一条的结束 <= 后一条的开始，
相邻引文允许首尾相接），在所有完整方案中最小化
``Σ |起点 − 提示起点|``；总偏差相同时取**起点序列字典序最小**的方案。任一
条引文在顺序与不重叠约束下找不到完整落位时整体不可行，不输出任何部分定位。

位置一律按 Unicode 码位（Python ``str`` 下标）计，区间为左闭右开
``[start, end)``；空引文与越界提示起点属非法输入，由调用方整体拒绝（422）。

出现查找用一次 **Aho–Corasick 多模式扫描**：把全部引文文本（按文本去重，
重复引文共享同一份出现列表）建 Trie 与失败链接，每个状态预聚合「在该状态
结束的全部模式」，正文从左到右扫描一次即可按起点顺序得到每条引文的全部
出现（含彼此重叠的出现，如 ``"aa"`` 在 ``"aaa"`` 中的 0、1）。相对逐条
``str.find``，避免重复正文（如 ``"ab" × 50_000``）下反复全串扫描的平方级
开销；正文长度 n、引文共 m 条时扫描为 O(n + 全部出现数)。

定位用按引文顺序逐层推进的**动态规划 + Pareto 前沿剪枝**。第 i 层状态为第 i
条引文的一次落位 ``(start, parent, cost)``，``parent`` 指向第 i-1 层所依据的
状态，``cost`` 为到该层为止的偏差总和。候选起点按出现位置升序处理：

- 父状态只能取「结束 <= 本候选起点」者；上一层前沿按起点升序保存、代价单调
  不降（见下），故可行父状态构成一个前缀，其**最后一个**同时具有最小代价与
  最小字典序（等代价平台上后写入者的前缀字典序更小），双指针推进即可 O(1)
  摊销取父，无需二分或枚举父状态；
- Pareto 剪枝：起点更小的状态结束也更早（同层引文等长），对后续落位更宽松。
  候选 s 被某个更早的保留状态支配当且仅当后者代价严格更小，或代价相同且起点
  序列字典序不更差。按起点升序扫描，只维护「已保留状态最小代价 + 该代价下
  最小字典序键」即可判定，被支配状态不可能出现在任何最优完整方案中；
- 字典序键为 ``(父状态在前沿中的字典序名次, 本层起点)``：第 i 层序列 =
  父序列 + [起点]，故该二元组与完整序列的字典序完全一致。每层扫描结束后，
  利用「同父状态的保留段连续且段内起点已升序」按父名次对段重排，赋予下一层
  使用的稠密名次。

最后一层前沿的末位状态即全局最优（代价最低；最低代价平台上字典序最小），沿
parent 指针回溯复原全部起点。规模上限（正文 50,000 码位、引文 32 条）内
每层状态数不超过正文长度，全程整数运算，相同输入（含引文的任何重复模式）
产生唯一结果。
"""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass

# 与 models.py 中请求模型的字段上限保持一致
MAX_TEXT_LEN = 50_000
MAX_QUOTES = 32
MAX_QUOTE_CHARS_TOTAL = 200_000


@dataclass(frozen=True)
class Quote:
    """一条引文：原样文本与提示起点（从 0 开始的 Unicode 码位位置）。"""

    text: str
    hint_start: int


@dataclass(frozen=True)
class QuoteLocationIssue:
    """一条非法输入原因（结构化，整体 422 时按引文下标顺序返回全部问题）。"""

    type: str
    quote_index: int
    # hint_out_of_range 时携带越界的提示起点；empty_quote 时为 None
    hint: int | None
    detail: str


@dataclass(frozen=True)
class QuoteSpan:
    """一条引文的定位结果：左闭右开区间 [start, end)（Unicode 码位）。"""

    index: int
    start: int
    end: int


@dataclass(frozen=True)
class QuoteLocationResult:
    # 输入非法时非空（确定性排序），其余字段不输出任何部分定位
    issues: tuple[QuoteLocationIssue, ...]
    feasible: bool
    locations: tuple[QuoteSpan, ...]
    total_deviation: int | None


def _validate(
    text_len: int,
    quotes: list[Quote],
) -> list[QuoteLocationIssue]:
    """收集非法输入：空引文 / 越界提示起点（每条引文按固定顺序最多两条）。"""
    issues: list[QuoteLocationIssue] = []
    for i, q in enumerate(quotes):
        if len(q.text) == 0:
            issues.append(QuoteLocationIssue(
                type="empty_quote",
                quote_index=i,
                hint=None,
                detail=(
                    f"第 {i} 条引文文本为空：引文必须包含至少一个 Unicode 码位，"
                    f"空引文无法在正文中定位"
                ),
            ))
        # 非空引文的合法起点为 0..text_len-1；正文为空时任何提示都越界
        if not 0 <= q.hint_start < text_len:
            if text_len > 0:
                detail = (
                    f"第 {i} 条引文的提示起点 {q.hint_start} 越界："
                    f"正文长度为 {text_len} 个 Unicode 码位，"
                    f"合法起点范围为 0..{text_len - 1}"
                )
            else:
                detail = (
                    f"第 {i} 条引文的提示起点 {q.hint_start} 越界："
                    f"正文为空（长度 0），不存在任何合法起点"
                )
            issues.append(QuoteLocationIssue(
                type="hint_out_of_range",
                quote_index=i,
                hint=q.hint_start,
                detail=detail,
            ))
    return issues


def _scan_occurrences(text: str, patterns: list[str]) -> list[array]:
    """Aho–Corasick 一次扫描求每个模式在 text 中的全部出现起点（升序）。

    patterns 为互不相同的非空字符串；返回与 patterns 同序的起点数组。正文自左
    向右扫描、在每个位置把「恰好在此结束」的模式起点 = 位置+1-长度 追加到其
    数组，故每个模式的数组天然按起点升序，且保留彼此重叠的出现。
    """
    # Trie：nexts[v] 为字符 -> 子节点；fail[v] 为失败链接；
    # outputs[v] 为在状态 v 结束（含沿失败链接可到达的后缀）的模式下标
    nexts: list[dict[str, int]] = [{}]
    fail: list[int] = [0]
    outputs: list[list[int]] = [[]]

    for pattern_index, pattern in enumerate(patterns):
        node = 0
        for ch in pattern:
            nxt = nexts[node].get(ch)
            if nxt is None:
                nxt = len(nexts)
                nexts[node][ch] = nxt
                nexts.append({})
                fail.append(0)
                outputs.append([])
            node = nxt
        outputs[node].append(pattern_index)

    # 失败链接（BFS）：根的直接子节点失败链接指向根；其余取父节点经失败链接
    # 转移的目标。先确定子节点失败链接，再把该状态的输出并入子节点输出，
    # 保证扫描时在任一状态读到的列表已含全部后缀命中。
    queue: deque[int] = deque()
    for child in nexts[0].values():
        fail[child] = 0
        queue.append(child)
    while queue:
        node = queue.popleft()
        for ch, child in nexts[node].items():
            target = fail[node]
            while target != 0 and ch not in nexts[target]:
                target = fail[target]
            fail[child] = nexts[target].get(ch, 0) if target != 0 or ch in nexts[0] else 0
            outputs[child].extend(outputs[fail[child]])
            queue.append(child)

    # goto(v, ch)：有显式边则走边；否则沿失败链接找（根上仍无则回到根）。
    # memo[v] 仅缓存隐式回退边，不与显式边混用。
    memo: list[dict[str, int] | None] = [None] * len(nexts)

    def goto(node: int, ch: str) -> int:
        direct = nexts[node].get(ch)
        if direct is not None:
            return direct
        cached = memo[node]
        if cached is not None and ch in cached:
            return cached[ch]
        target = fail[node]
        while target != 0 and ch not in nexts[target]:
            target = fail[target]
        result = nexts[target].get(ch, 0)
        if cached is None:
            cached = {}
            memo[node] = cached
        cached[ch] = result
        return result

    positions: list[array] = [array("i") for _ in patterns]
    pattern_len = [len(p) for p in patterns]
    node = 0
    for pos, ch in enumerate(text):
        node = goto(node, ch)
        for pattern_index in outputs[node]:
            # 命中模式在 pos 处结束 -> 起点为 pos + 1 - 长度
            positions[pattern_index].append(pos + 1 - pattern_len[pattern_index])
    return positions


def locate_quotes(
    manuscript_id: str,
    text: str,
    quotes: list[Quote],
) -> QuoteLocationResult:
    """校验输入并求最优（最小总偏差、同偏差字典序最小）的完整引文定位。

    存在非法输入时 issues 非空（按引文下标顺序），调用方应整体拒绝（422）；
    输入合法但无完整方案时 issues 为空、feasible=False（不输出部分定位）。
    ``manuscript_id`` 仅参与无状态回显，不影响定位计算。
    """
    del manuscript_id  # 无状态服务：不据 ID 查询或持久化任何稿件

    issues = _validate(len(text), quotes)
    if issues:
        return QuoteLocationResult(
            issues=tuple(issues), feasible=False, locations=(), total_deviation=None
        )

    n_quotes = len(quotes)
    if n_quotes == 0:
        # 空引文组的完整方案就是空方案，总偏差 0
        return QuoteLocationResult(
            issues=(), feasible=True, locations=(), total_deviation=0
        )

    # 引文文本去重后共享出现列表；重复引文各自参与 DP，只是出现集合相同
    distinct: dict[str, int] = {}
    pattern_index: list[int] = []
    for q in quotes:
        idx = distinct.get(q.text)
        if idx is None:
            idx = len(distinct)
            distinct[q.text] = idx
        pattern_index.append(idx)
    patterns = [""] * len(distinct)
    for pattern_text, idx in distinct.items():
        patterns[idx] = pattern_text
    all_positions = _scan_occurrences(text, patterns)

    # 每层四个并行数组（下标 = 该层前沿状态序号）：
    #   pos_layers[i]    第 i 层状态的引文起点
    #   parent_layers[i] 父状态在上一层数组中的下标（第 0 层为 -1）
    #   cost_layers[i]   到该状态为止的偏差总和
    # ranks 仅在构造下一层时需要：当前层状态按「完整起点序列」字典序的稠密名次。
    # 第 0 层无父状态，整体单独处理一次（无指针、无父名次），主循环只跑 i>=1。
    pos_layers: list[array] = []
    parent_layers: list[array] = []
    cost_layers: list[array] = []

    KEY_BASE = len(text) + 1

    # ---- 第 0 层：对每个出现直接取 |起点 - 提示|，同样做 Pareto 剪枝 ----
    first = all_positions[pattern_index[0]]
    hint0 = quotes[0].hint_start
    pos0: list[int] = []
    cost0: list[int] = []
    min_cost = -1
    best_key = 0
    for s in first:
        cost = s - hint0 if s >= hint0 else hint0 - s
        key = s  # 第 0 层父名次恒为 0，键即起点
        if min_cost >= 0 and (cost > min_cost or (cost == min_cost and key >= best_key)):
            continue
        pos0.append(s)
        cost0.append(cost)
        min_cost = cost
        best_key = key
    if not pos0:
        return QuoteLocationResult(
            issues=(), feasible=False, locations=(), total_deviation=None
        )
    pos_layers.append(array("i", pos0))
    parent_layers.append(array("i", [-1] * len(pos0)))
    cost_layers.append(array("i", cost0))
    # 第 0 层只有一个虚父：起点升序即字典序
    prev_ranks = list(range(len(pos0)))
    quote_len_prev = len(quotes[0].text)

    for layer in range(1, n_quotes):
        q = quotes[layer]
        occurrences = all_positions[pattern_index[layer]]
        quote_len = len(q.text)

        # 热路径层用 Python list（逐项 append / 下标访问比 array 快，最坏输入下
        # 每层最多约 n 个状态），层定稿时再固化为紧凑 array 供回溯。
        pos_list: list[int] = []
        parent_list: list[int] = []
        cost_list: list[int] = []

        # 已保留状态中的最小代价，以及该代价下最小的字典序键；
        # 前沿按起点升序追加，代价整体单调不降（等代价平台上键严格递减）。
        # 字典序键 (父名次, 起点) 编码为单个整数 rank * KEY_BASE + 起点：
        # 起点 < KEY_BASE，编码后数值顺序与二元组字典序一致，避免逐层逐候选
        # 分配元组（最坏输入下可达数百万次）。
        min_cost = -1
        best_key = 0

        parent_idx = -1  # 「结束 <= 当前候选起点」的最后一个上一层状态
        prev_pos = pos_layers[-1]
        prev_cost = cost_layers[-1]
        prev_limit = len(prev_pos)
        ranks_prev = prev_ranks

        pos_append = pos_list.append
        parent_append = parent_list.append
        cost_append = cost_list.append
        hint = q.hint_start

        for s in occurrences:
            # 下一候选起点只会更大，双指针单调推进
            limit = s
            while (
                parent_idx + 1 < prev_limit
                and prev_pos[parent_idx + 1] + quote_len_prev <= limit
            ):
                parent_idx += 1
            if parent_idx < 0:
                continue  # 没有任何前文落位能在不重叠前提下接到 s
            parent_cost = prev_cost[parent_idx]
            cost = parent_cost + (s - hint if s >= hint else hint - s)
            key = ranks_prev[parent_idx] * KEY_BASE + s

            if min_cost >= 0 and (
                cost > min_cost or (cost == min_cost and key >= best_key)
            ):
                continue  # 被某个更早且更优（或字典序不更差）的状态支配

            pos_append(s)
            parent_append(parent_idx)
            cost_append(cost)
            min_cost = cost  # 保留条件保证新代价 <= 旧最小值
            best_key = key

        if not pos_list:
            # 该层不存在任何可落位状态 -> 无完整方案，不输出部分定位
            return QuoteLocationResult(
                issues=(), feasible=False, locations=(), total_deviation=None
            )

        # 计算本层状态的字典序稠密名次供下一层使用。父状态下标随候选起点
        # 单调不减，故同父的保留状态在数组中构成连续段且段内起点已升序；
        # 完整序列顺序 = 先按父名次排段、段内再按起点。
        groups: list[tuple[int, int, int]] = []  # (父名次, 段起, 段止)
        run_lo = 0
        prev_parent = parent_list[0]
        for idx in range(1, len(pos_list)):
            cur_parent = parent_list[idx]
            if cur_parent != prev_parent:
                groups.append((ranks_prev[prev_parent], run_lo, idx))
                run_lo = idx
                prev_parent = cur_parent
        groups.append((ranks_prev[prev_parent], run_lo, len(pos_list)))

        ranks = [0] * len(pos_list)
        rank = 0
        for _, lo, hi in sorted(groups, key=lambda g: g[0]):
            for idx in range(lo, hi):
                ranks[idx] = rank
                rank += 1

        # 本层定稿：热路径列表固化为紧凑整数数组，仅保留回溯必需的层
        pos_layers.append(array("i", pos_list))
        parent_layers.append(array("i", parent_list))
        cost_layers.append(array("i", cost_list))
        prev_ranks = ranks
        quote_len_prev = quote_len

    # 末层前沿末位：代价最低（单调不降的尾段）且最低代价平台上字典序最小
    state_idx = len(pos_layers[-1]) - 1
    total_deviation = cost_layers[-1][state_idx]
    starts = [0] * n_quotes
    for layer in range(n_quotes - 1, -1, -1):
        starts[layer] = pos_layers[layer][state_idx]
        state_idx = parent_layers[layer][state_idx]

    locations = tuple(
        QuoteSpan(index=i, start=starts[i], end=starts[i] + len(quotes[i].text))
        for i in range(n_quotes)
    )
    return QuoteLocationResult(
        issues=(),
        feasible=True,
        locations=locations,
        total_deviation=total_deviation,
    )
