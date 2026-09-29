"""讨论时段排期（POST /schedule-discussions）。

接收 `/assign` 的可行分配结果（至多 8 篇稿件及其评审人名单）、至多 8 个候选
讨论时段（各带非负整数容量）与每位评审人可出席的时段，在以下全局约束下求完整排期：

- 每篇稿件恰好排入一个时段；
- 稿件的全部评审人在该时段均可出席；
- 同一评审人在同一时段最多讨论一篇稿件（同一时段该评审人被排的多篇稿件争用
  其唯一的「席位」）；
- 每个时段被排入的稿件数不超过该时段容量。

多个可行方案按「稿件 ID 升序、逐位比较对应时段 ID」的字典序比较，返回字典序
最小者；输入列表的排列方式不影响结果（所有集合先按 ID 排序后处理）。不存在
完整方案时返回不可行，不输出部分排期。

求解用「按稿件 ID 排序、逐位枚举按 ID 排序的可行时段」的回溯：第一个找到的
完整解天然是字典序最小方案。每次落位后做前向可行性剪枝（松弛掉剩余稿件彼此
之间的评审人席位争用，只保留出席交集、与已排前缀的争用、时段剩余容量）：

1. 每篇剩余稿件至少有一个可去时段；
2. 时段侧 Hall 必要条件：全部剩余稿件邻接时段的剩余容量之和不少于稿件数；
3. 评审人侧 Hall 必要条件：把每位评审人在其可出席的每个时段视为容量 1 的
   右点，凡可行时段全落在某时段集合 T 内且含该评审人的稿件，数量不得超过
   T 内该评审人仍可服务的时段数（子集位掩码 + 子集和 DP，至多 8 个时段）；
4. 带容量二分图匹配（Kuhn 增广路，右侧按时段容量复制）存在覆盖全部剩余
   稿件的匹配时，该前缀才可能扩展为完整解。

任一层失败即回溯，任一层通过都是真实问题可行的必要条件（不错杀完整解）。
规模受输入上限约束（稿件、时段各至多 8 个），全部判断只用整数与位掩码。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_MANUSCRIPTS = 8
MAX_SLOTS = 8


@dataclass(frozen=True)
class DiscussionAssignment:
    """一篇已完成分配的稿件及其评审人名单（取自 /assign 的可行结果）。"""

    manuscript_id: str
    reviewer_ids: tuple[str, ...]


@dataclass(frozen=True)
class DiscussionSlot:
    """一个候选讨论时段及其非负整数稿件容量。"""

    id: str
    capacity: int


@dataclass(frozen=True)
class ReviewerAvailability:
    """一位评审人可出席的时段 ID 列表。"""

    reviewer_id: str
    slot_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScheduleIssue:
    """一条非法输入原因（结构化，整体 422 时返回全部问题）。"""

    type: str
    # 问题涉及的稿件 / 时段 / 评审人 ID；不适用时为 None
    manuscript_id: str | None
    slot_id: str | None
    reviewer_id: str | None
    detail: str


@dataclass(frozen=True)
class ScheduleResult:
    issues: tuple[ScheduleIssue, ...]
    # 可行时为 (稿件 ID -> 时段 ID) 的完整排期（按稿件 ID 排序）；否则为空
    schedule: tuple[tuple[str, str], ...]


def _issue_sort_key(issue: ScheduleIssue) -> tuple[str, str, str, str]:
    return (
        issue.type,
        issue.manuscript_id or "",
        issue.slot_id or "",
        issue.reviewer_id or "",
    )


def solve_discussion_schedule(
    assignments: list[DiscussionAssignment],
    slots: list[DiscussionSlot],
    availability: list[ReviewerAvailability],
) -> ScheduleResult:
    """校验输入并求字典序最小的完整排期。

    存在任何非法输入时 issues 非空（确定性排序），schedule 为空，调用方应整体
    拒绝（422）；输入合法但无完整方案时 issues 为空、schedule 也为空（不可行，
    不输出部分排期）。
    """
    issues: list[ScheduleIssue] = []

    # ---- 1. 数量上限（在重复项检查之前即计数，超限仍继续收集其余问题） ----
    if len(assignments) > MAX_MANUSCRIPTS:
        issues.append(ScheduleIssue(
            type="too_many_manuscripts",
            manuscript_id=None,
            slot_id=None,
            reviewer_id=None,
            detail=(
                f"稿件数 {len(assignments)} 超出上限 {MAX_MANUSCRIPTS}，"
                f"一次至多排入 {MAX_MANUSCRIPTS} 篇稿件"
            ),
        ))
    if len(slots) > MAX_SLOTS:
        issues.append(ScheduleIssue(
            type="too_many_slots",
            manuscript_id=None,
            slot_id=None,
            reviewer_id=None,
            detail=(
                f"候选时段数 {len(slots)} 超出上限 {MAX_SLOTS}，"
                f"一次至多给出 {MAX_SLOTS} 个候选时段"
            ),
        ))

    # ---- 2. 重复 ID：稿件、时段各自不得重复 ----
    seen_manuscript: set[str] = set()
    duplicate_manuscripts: set[str] = set()
    for a in assignments:
        if a.manuscript_id in seen_manuscript:
            duplicate_manuscripts.add(a.manuscript_id)
        seen_manuscript.add(a.manuscript_id)
    for mid in sorted(duplicate_manuscripts):
        issues.append(ScheduleIssue(
            type="duplicate_manuscript",
            manuscript_id=mid,
            slot_id=None,
            reviewer_id=None,
            detail=f"稿件 {mid} 在分配名单中重复出现，每篇稿件至多一条记录",
        ))

    seen_slot: set[str] = set()
    duplicate_slots: set[str] = set()
    for s in slots:
        if s.id in seen_slot:
            duplicate_slots.add(s.id)
        seen_slot.add(s.id)
    for sid in sorted(duplicate_slots):
        issues.append(ScheduleIssue(
            type="duplicate_slot",
            manuscript_id=None,
            slot_id=sid,
            reviewer_id=None,
            detail=f"时段 {sid} 重复出现，每个时段 ID 至多定义一次",
        ))

    # 可用性条目以评审人去重键（重复条目整体只取一次并作为问题报告）；
    # 时段重复 ID 时后续「未知时段」校验基于去重后的时段集合。
    slot_ids = {s.id for s in slots}
    seen_reviewer: set[str] = set()
    duplicate_availability: set[str] = set()
    availability_map: dict[str, set[str]] = {}
    for av in availability:
        rid = av.reviewer_id
        if rid in seen_reviewer:
            duplicate_availability.add(rid)
            continue
        seen_reviewer.add(rid)
        availability_map[rid] = set(av.slot_ids)
    for rid in sorted(duplicate_availability):
        issues.append(ScheduleIssue(
            type="duplicate_availability",
            manuscript_id=None,
            slot_id=None,
            reviewer_id=rid,
            detail=f"评审人 {rid} 的可出席时段重复声明，每位评审人至多一条记录",
        ))

    # ---- 3. 可用时段引用未知时段 ----
    unknown_refs: set[tuple[str, str]] = set()
    for rid, declared in availability_map.items():
        for sid in declared:
            if sid not in slot_ids:
                unknown_refs.add((sid, rid))
    for sid, rid in sorted(unknown_refs):
        issues.append(ScheduleIssue(
            type="unknown_slot_reference",
            manuscript_id=None,
            slot_id=sid,
            reviewer_id=rid,
            detail=(
                f"评审人 {rid} 的可出席时段 {sid} 未在候选时段中定义，"
                f"可用时段只能引用已声明的时段 ID"
            ),
        ))

    # ---- 4. 分配名单中的评审人未声明可出席时段 ----
    undeclared: set[str] = set()
    for a in assignments:
        for rid in set(a.reviewer_ids):
            if rid not in availability_map:
                undeclared.add(rid)
    for rid in sorted(undeclared):
        issues.append(ScheduleIssue(
            type="reviewer_availability_undeclared",
            manuscript_id=None,
            slot_id=None,
            reviewer_id=rid,
            detail=(
                f"评审人 {rid} 出现在分配名单中但未声明可出席时段，"
                f"每位参与讨论的评审人都必须给出可出席的时段列表"
            ),
        ))

    if issues:
        issues.sort(key=_issue_sort_key)
        return ScheduleResult(issues=tuple(issues), schedule=())

    # 空输入的边界情形：无稿件即完整排期（空方案，字典序最小）；
    # 有稿件而无时段必然不可行（下面的回溯自然返回空），无需特判。
    manuscript_ids = sorted({a.manuscript_id for a in assignments})
    slot_id_list = sorted(slot_ids)
    n_slots = len(slot_id_list)
    all_slot_bits = (1 << n_slots) - 1
    slot_index = {sid: i for i, sid in enumerate(slot_id_list)}
    slot_capacities = {s.id: s.capacity for s in slots}

    # 稿件 -> 去重评审人集合（名单内重复 ID 视为同一评审人）
    reviewers_of: dict[str, frozenset[str]] = {}
    for a in assignments:
        reviewers_of[a.manuscript_id] = frozenset(a.reviewer_ids)

    # 评审人 -> 可出席时段位掩码
    availability_bits: dict[str, int] = {
        rid: sum(1 << slot_index[sid] for sid in declared)
        for rid, declared in availability_map.items()
    }

    # 稿件 -> 其全体评审人共同可出席的时段位掩码
    feasible_bits: dict[str, int] = {}
    for mid in manuscript_ids:
        bits = all_slot_bits  # 无评审人的稿件不受出席约束
        for rid in reviewers_of[mid]:
            bits &= availability_bits[rid]
        feasible_bits[mid] = bits

    capacities = [slot_capacities[sid] for sid in slot_id_list]

    # 回溯状态（按时段下标索引）：
    #   placed_count[j] = 已排入时段 j 的稿件数；
    #   busy_mask[r]    = 评审人 r 已占用的时段位掩码（同一时段至多一篇）。
    placed_count = [0] * n_slots
    busy_mask: dict[str, int] = {}
    chosen: list[int] = []

    # 时段子集容量查表在每次剪枝时按当时剩余容量构造（至多 256 项）。

    def _matching_feasible(pos: int) -> bool:
        """松弛问题的完整匹配可行性（剩余稿件 -> 时段的带容量二分图匹配）。

        忽略「剩余稿件彼此之间」的评审人席位争用（回溯逐稿落位时仍会逐对
        检查），只考虑：全体评审人可出席、与**已排前缀**无评审人争用、时段
        剩余容量。松弛问题可行是真实问题可行的必要条件，作为前向剪枝安全。剪枝
        分四层，由廉价到昂贵逐层过滤：

        1. 每篇剩余稿件至少有一个可去时段；
        2. 时段侧 Hall 必要条件：全部剩余稿件邻接时段的**剩余**容量之和不少于
           稿件数（子集位掩码查表，对最常见的「根本放不下」死分支立即失败）；
        3. 评审人侧 Hall 必要条件（见下方实现注释）；
        4. 带容量 Kuhn 增广路求完整匹配（右侧按时段容量复制），通过才确认可行。
        """
        remaining = manuscript_ids[pos:]
        r = len(remaining)
        if r == 0:
            return True

        free_cap = [capacities[j] - placed_count[j] for j in range(n_slots)]
        full_slot_bits = 0
        for j in range(n_slots):
            if free_cap[j] <= 0:
                full_slot_bits |= 1 << j

        # 时段子集 -> 剩余容量之和（位掩码递推，至多 256 项）
        slot_seat_sum = [0] * (1 << n_slots)
        for t in range(1, 1 << n_slots):
            low = t & -t
            j = low.bit_length() - 1
            slot_seat_sum[t] = slot_seat_sum[t ^ low] + free_cap[j]

        # edges[k]：剩余第 k 篇稿件（在当前已排前缀下）可去的时段位掩码
        edges: list[int] = []
        union = 0
        for mid in remaining:
            bits = feasible_bits[mid]
            for rid in reviewers_of[mid]:
                bits &= ~busy_mask.get(rid, 0)
            bits &= ~full_slot_bits  # 已满容量的时段不可用
            if bits == 0:
                return False
            edges.append(bits)
            union |= bits

        # 第 2 层：时段侧 Hall
        if slot_seat_sum[union] < r:
            return False

        # 第 3 层：评审人席位 Hall 必要条件。把每位评审人在其可出席的每个
        # 时段视为一个容量 1 的右点（稿件同时占用其全体评审人的右点）。对任一
        # 评审人 x 与时段集合 T，凡「包含 x 且全体评审人共同可出席的可行时段
        # 全在 T 内」的稿件都必须占用 x 在 T 内的某个席位——x 在每个时段
        # 至多 1 席，故这类稿件数不得超过 T 内 x 仍可服务的时段数（可出席、
        # 未被已排前缀占用、时段未满）。案例「8 篇共享同一评审人、仅 7 个
        # 时段」在根节点即被剪。实现：逐评审人 x 以「含 x 的剩余稿件」的
        # 可行掩码（edges[k]）计数，子集和 DP 求每个 T 下可行掩码 ⊆ T 的
        # 稿件数，与 T & x 可用时段掩码的位数比较。
        reviewer_feasible_count: dict[str, list[int]] = {}
        for k, mid in enumerate(remaining):
            for rid in reviewers_of[mid]:
                reviewer_feasible_count.setdefault(
                    rid, [0] * (1 << n_slots)
                )[edges[k]] += 1
        for rid, counts in reviewer_feasible_count.items():
            usable = (
                availability_bits[rid]
                & ~busy_mask.get(rid, 0)
                & ~full_slot_bits
            )
            contained = counts[:]
            for j in range(n_slots):
                bit = 1 << j
                for t in range(1 << n_slots):
                    if t & bit:
                        contained[t] += contained[t ^ bit]
            for t in range(1, 1 << n_slots):
                if contained[t] > (t & usable).bit_count():
                    return False

        # 第 4 层：带容量 Kuhn。match[k] 为稿件 k 匹配的时段，
        # used[j] 为时段 j 已被剩余稿件占用的席位数
        match = [-1] * r
        used = [0] * n_slots

        def find(k: int, seen: int) -> bool:
            candidates = edges[k] & ~seen
            while candidates:
                low = candidates & -candidates
                j = low.bit_length() - 1
                candidates -= low
                seen |= low
                if used[j] < free_cap[j]:
                    used[j] += 1
                    match[k] = j
                    return True
                # 席位已满：尝试把已占用该席位的某篇稿件改配到别处
                for other in range(r):
                    if match[other] == j and find(other, seen):
                        match[k] = j
                        return True
            return False

        for k in range(r):
            if not find(k, 0):
                return False
        return True

    def backtrack(pos: int) -> bool:
        if pos == len(manuscript_ids):
            return True
        mid = manuscript_ids[pos]
        rids = reviewers_of[mid]
        candidates = feasible_bits[mid]
        while candidates:
            low = candidates & -candidates
            j = low.bit_length() - 1
            candidates -= low
            # 容量约束
            if placed_count[j] >= capacities[j]:
                continue
            # 评审人同时段唯一席位：任一评审人已在该时段讨论另一稿即冲突
            if any((busy_mask.get(rid, 0) >> j) & 1 for rid in rids):
                continue
            placed_count[j] += 1
            for rid in rids:
                busy_mask[rid] = busy_mask.get(rid, 0) | (1 << j)
            chosen.append(j)
            # 前向剪枝：剩余稿件必须仍存在完整匹配，该前缀才可能扩展为完整解
            if _matching_feasible(pos + 1) and backtrack(pos + 1):
                return True
            chosen.pop()
            for rid in rids:
                busy_mask[rid] &= ~(1 << j)
                if busy_mask[rid] == 0:
                    del busy_mask[rid]
            placed_count[j] -= 1
        return False

    if not manuscript_ids or backtrack(0):
        schedule = tuple(
            (mid, slot_id_list[chosen[i]])
            for i, mid in enumerate(manuscript_ids)
        )
        return ScheduleResult(issues=(), schedule=schedule)

    # 无完整方案：不输出部分排期
    return ScheduleResult(issues=(), schedule=())
