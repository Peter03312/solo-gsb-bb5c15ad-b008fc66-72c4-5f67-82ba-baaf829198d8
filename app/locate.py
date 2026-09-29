"""评审引文定位（POST /locate-quotes）。

评审意见引用稿件原文时，需要把一组**按正文顺序排列**的引文整体定位到同一篇
稿件。输入稿件原文与每条引文的原样文本及提示起点（从 0 开始的 Unicode 字符
位置），为每条引文选择一个**完全一致**（逐字符相等）的出现位置，整组位置：

- 按引文顺序排列（后一条起点不早于前一条终点）；
- 互不重叠（后一条起点 >= 前一条起点 + 前一条长度）。

在所有完整方案中最小化 Σ|实际起点 - 提示起点|；同分时取起点序列**字典序
最小**的方案。任一条引文无完全一致出现，或不存在满足顺序 / 不重叠约束的完整
方案时返回不可行，**不输出任何部分定位**。

求解用按引文顺序的动态规划（稀疏层）。记 starts_i 为第 i 条引文在正文中全部
完全一致出现的起点（升序），定义后缀最优：

    suff_i(p) = 引文 i..n-1 的起点全部 >= p 时，最小偏差总和

第 i 条取出现 s_k 时的代价为

    cost_i(k) = |s_k - hint_i| + suff_{i+1}(s_k + len_i)

s + len_i 即下一条引文允许的最小起点（不重叠边界）。suff_i(p) 即全部
s_k >= p 的 cost_i(k) 最小值，故每层只需在**出现下标**上保存后缀最小值
suffmin_i[k] = min(cost_i(j) : j >= k)（末位补 INF）；对任意边界 p 点查
下一层时，因扫描 k 递减、查询边界 s_k + len_i 也单调不增，用一个只减不增的
归并指针即可线性完成，无需长度 L 的稠密数组——时间、内存只与全部出现总数
相关。suff_n(p) 恒为 0；suff_0(0) 为无穷即不可行。

出现位置枚举：相同引文文本只枚举一次（缓存，不同下标的同一文本共用同一张
升序出现表，以紧凑 int 数组保存）。枚举优先用 C 层 ``str.find``；当出现数
超过与正文 / 模式长度相关的保护阈值（典型于长模式在周期文本中的高度重叠，
反复 find 会退化为近平方）时回退到线性时间的 KMP。

字典序最小方案在最优值确定后正向贪心重构：位置 i 从首个 >= 上一条终点的
出现起，取第一个使 cost_i(k) == suff_i(lo_i) 的出现（出现升序，第一个即
起点最小者），再把 lo 推进到 s_k + len_i；下一层点查同样用只增不减的归并
指针，全部点查合计线性。前缀固定、剩余取最优时当前位越小字典序越小，故逐位
取最小可行起点即得全局字典序最小序列。所有位置均为 Python str 的 Unicode
码点下标（与字符位置语义一致），计算全程整数运算，相同输入必然产生相同输出。
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass

# 与 models.py 中请求字段上限保持一致
MAX_TEXT_LENGTH = 50_000
MAX_QUOTES = 100

# 64 位整数「无穷」：单条偏差至多 MAX_TEXT_LENGTH，合计远小于该值
_INF = 1 << 62


@dataclass(frozen=True)
class QuoteSpec:
    """一条引文：原样文本与提示起点（从 0 开始的 Unicode 字符位置）。"""

    text: str
    hint_start: int


@dataclass(frozen=True)
class QuoteLocationIssue:
    """一条非法输入原因（结构化，整体 422 时返回全部问题）。"""

    type: str
    # 问题引文在 quotes 列表中的下标
    index: int
    detail: str


@dataclass(frozen=True)
class QuotePlacement:
    """一条引文的定位结果。

    start 为含端点、end 为排他端点，引文占据原文的 [start, end) 字符区间，
    end - start 恰为引文字符数；deviation = |start - hint_start|。
    """

    index: int
    start: int
    end: int
    deviation: int


@dataclass(frozen=True)
class QuoteLocationResult:
    # 非法输入时非空（确定性排序），调用方应整体拒绝（422）
    issues: tuple[QuoteLocationIssue, ...]
    # 输入合法且存在完整方案时为 True
    feasible: bool
    # 可行时为按引文顺序排列的完整定位；不可行 / 非法输入时为空（不输出部分定位）
    placements: tuple[QuotePlacement, ...]
    # 可行时为偏差总和；否则为 0
    total_deviation: int


def _issue_sort_key(issue: QuoteLocationIssue) -> tuple[str, int]:
    return issue.type, issue.index


def _kmp_prefix(pattern: str) -> list[int]:
    """KMP 前缀函数（失配回退表）。"""
    m = len(pattern)
    pi = [0] * m
    j = 0
    for i in range(1, m):
        while j and pattern[i] != pattern[j]:
            j = pi[j - 1]
        if pattern[i] == pattern[j]:
            j += 1
        pi[i] = j
    return pi


def _enumerate_starts(text: str, pattern: str) -> array:
    """枚举 pattern 在 text 中的全部完全一致出现（含重叠），起点升序。

    优先 str.find（C 层快速搜索）；find 的出现数超过保护阈值后回退 KMP
    线性扫描——长模式在高度周期的正文中反复 find 会退化为近平方，正常
    文本的出现数远低于阈值故从不触发回退。
    """
    text_len = len(text)
    qlen = len(pattern)
    # 正常情况下一条引文的出现数约为 L/qlen 量级；给出宽限常数倍后仍超出，
    # 即视为高度重叠（find 退化风险），改由 KMP 完成
    find_cap = max(64, 8 * (text_len // qlen + 1))
    starts: array = array("i")
    pos = text.find(pattern)
    while pos != -1:
        starts.append(pos)
        if len(starts) > find_cap:
            starts = array("i")
            pi = _kmp_prefix(pattern)
            j = 0
            for i in range(text_len):
                ch = text[i]
                while j and ch != pattern[j]:
                    j = pi[j - 1]
                if ch == pattern[j]:
                    j += 1
                    if j == qlen:
                        starts.append(i - qlen + 1)
                        j = pi[j - 1]
            return starts
        pos = text.find(pattern, pos + 1)
    return starts


def locate_quotes(
    text: str,
    quotes: list[QuoteSpec],
) -> QuoteLocationResult:
    """校验输入并求偏差总和最小、同分字典序最小的完整引文定位。

    存在任何非法输入（空引文 / 提示起点越界）时 issues 非空（确定性排序），
    调用方应整体拒绝（422）；输入合法但无完整方案时 issues 为空、feasible
    为 False，且不输出任何部分定位。
    """
    issues: list[QuoteLocationIssue] = []
    text_len = len(text)

    # ---- 1. 非法输入收集：空引文、提示起点越界（合法范围 0..len(text)，
    #         len(text) 表示文末边界；同一引文可同时命中两类问题） ----
    for i, q in enumerate(quotes):
        if len(q.text) == 0:
            issues.append(QuoteLocationIssue(
                type="empty_quote",
                index=i,
                detail=(
                    f"第 {i} 条引文的 text 为空字符串，引文必须是非空原文片段"
                ),
            ))
        if q.hint_start < 0 or q.hint_start > text_len:
            issues.append(QuoteLocationIssue(
                type="hint_out_of_bounds",
                index=i,
                detail=(
                    f"第 {i} 条引文的 hint_start={q.hint_start} 越界："
                    f"正文长度为 {text_len} 个字符，合法范围为 0..{text_len}"
                ),
            ))

    if issues:
        issues.sort(key=_issue_sort_key)
        return QuoteLocationResult(
            issues=tuple(issues), feasible=False, placements=(), total_deviation=0
        )

    # 空引文组：完整方案就是空方案，偏差总和 0
    if not quotes:
        return QuoteLocationResult(
            issues=(), feasible=True, placements=(), total_deviation=0
        )

    n = len(quotes)

    # ---- 2. 枚举每条引文的全部完全一致出现（按文本去重缓存）；任一无出现即不可行 ----
    # 同一条原文片段可能被多条引文引用：出现表完全相同，只枚举一次。
    starts_cache: dict[str, array] = {}
    starts_of: list[array] = []
    for q in quotes:
        starts = starts_cache.get(q.text)
        if starts is None:
            starts = _enumerate_starts(text, q.text)
            if len(starts) == 0:
                # 不输出任何部分定位
                return QuoteLocationResult(
                    issues=(), feasible=False, placements=(), total_deviation=0
                )
            starts_cache[q.text] = starts
        starts_of.append(starts)

    # ---- 3. 稀疏后缀 DP：自尾向头逐层计算 cost_i(k) 与 suffmin_i ----
    # 对下一层 suff_{i+1}(s_k + len_i) 的点查用单调归并指针完成：
    # k 递减时边界 s_k + len_i 单调不增，指针 t（首个 nxt_starts[t] >= 边界）
    # 也只减不增，整层指针至多移动 m_{i+1} 次。
    lengths = [len(q.text) for q in quotes]
    hints = [q.hint_start for q in quotes]
    suff_layers: list[array] = [array("q")] * n
    inf = _INF
    abs_ = abs
    for i in range(n - 1, -1, -1):
        starts = starts_of[i]
        qlen = lengths[i]
        hint = hints[i]
        m = len(starts)
        suffmin: array = array("q", [0]) * (m + 1)
        suffmin[m] = inf
        best = inf
        if i + 1 < n:
            nxt_starts = starts_of[i + 1]
            nxt_suffmin = suff_layers[i + 1]
            nxt_m = len(nxt_starts)
            t = nxt_m
            for k in range(m - 1, -1, -1):
                boundary = starts[k] + qlen
                while t > 0 and nxt_starts[t - 1] >= boundary:
                    t -= 1
                tail = nxt_suffmin[t] if t < nxt_m else inf
                value = abs_(starts[k] - hint) + tail
                if value < best:
                    best = value
                suffmin[k] = best
        else:
            # 基底层 suff_n(p) 恒为 0
            for k in range(m - 1, -1, -1):
                value = abs_(starts[k] - hint)
                if value < best:
                    best = value
                suffmin[k] = best
        suff_layers[i] = suffmin

    optimum = suff_layers[0][0]
    if optimum >= inf:
        # 顺序 + 不重叠约束下无完整方案：不输出任何部分定位
        return QuoteLocationResult(
            issues=(), feasible=False, placements=(), total_deviation=0
        )

    # ---- 4. 字典序最小最优方案的正向贪心重构 ----
    # 第 i 位从首个 >= lo 的出现起，取第一个使 cost_i(k) == suff_i(lo) 的
    # 出现（出现表升序，第一个即起点最小者）；对下一层 suff_{i+1}(s+len_i)
    # 的点查用单调归并指针（k 递增时边界单调不减，指针只增不减）。
    placements: list[QuotePlacement] = []
    lo = 0
    for i in range(n):
        starts = starts_of[i]
        qlen = lengths[i]
        hint = hints[i]
        suffmin = suff_layers[i]
        m = len(starts)

        # 推进到首个起点 >= lo 的出现，其下标即 k0
        k = 0
        while k < m and starts[k] < lo:
            k += 1
        target = suffmin[k] if k < m else inf

        chosen: int | None = None
        chosen_dev = 0
        if i + 1 < n:
            nxt_starts = starts_of[i + 1]
            nxt_suffmin = suff_layers[i + 1]
            nxt_m = len(nxt_starts)
            t = 0
            while k < m:
                s = starts[k]
                boundary = s + qlen
                while t < nxt_m and nxt_starts[t] < boundary:
                    t += 1
                tail = nxt_suffmin[t] if t < nxt_m else inf
                deviation = abs_(s - hint)
                if deviation + tail == target:
                    chosen, chosen_dev = s, deviation
                    break
                k += 1
        else:
            while k < m:
                s = starts[k]
                deviation = abs_(s - hint)
                if deviation == target:
                    chosen, chosen_dev = s, deviation
                    break
                k += 1

        # DP 最优值有限则每个贪心步必然找到候选
        assert chosen is not None
        placements.append(QuotePlacement(
            index=i,
            start=chosen,
            end=chosen + qlen,
            deviation=chosen_dev,
        ))
        lo = chosen + qlen

    return QuoteLocationResult(
        issues=(),
        feasible=True,
        placements=tuple(placements),
        total_deviation=optimum,
    )
