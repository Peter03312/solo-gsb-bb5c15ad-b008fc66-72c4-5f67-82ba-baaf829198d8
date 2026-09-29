"""评审工时钟重放（POST /review-clock）。

对 /assign 名单形成的每个「稿件—评审人」配对，按 UTC 事件时间重放其评审状态机，
并按统一工作日历累计工作时长：

- 工作日历：周一至周五（UTC）09:00—17:00 为工作时段（每个工作日 8 小时），
  请求给出的节假日整日不计工作时间；
- 状态机：pending（待接受）→ active（已接受/计时中）⇄ paused（已暂停）
  → submitted（已提交）。仅计时中（active）的时段累计工作时长，暂停期间
  停止计时；跨下班、周末与节假日一律顺延，不消耗工时预算；
- 预计截止时刻：进入计时段（accept / resume）时，以该段起点与当时剩余工时
  预算投影到工作日历上得到的 UTC 时刻；暂停中无法预计（截止时刻为空），
  恢复后以恢复时刻与剩余预算重新投影；提交后冻结为提交前所在段的投影；
- 逾期：计时中且查询时刻晚于预计截止时刻；提交后以提交时刻判定
  （等价于提交时累计工时已超过预算）。

事件校验（任一不满足即整体拒绝，返回全部结构化问题、不输出任何钟结果）：

- duplicate_pair：pairs 中同一 (稿件, 评审人) 配对重复出现；
- unknown_pair：事件引用了未在 pairs 中声明的配对；
- duplicate_event：同一配对在同一 UTC 时刻出现两条及以上事件（与事件类型无关）；
- invalid_transition：事件在当前状态下不构成合法状态转换。

查询时刻 query_at 是「截至时刻」：仅重放 at <= query_at 的事件（之后的事件
整段忽略，既不改变状态也不参与校验），钟面结果反映查询时刻的快照。全部计算
使用整数秒与时区感知的 UTC 时刻，结果与事件输入顺序无关，只取决于事件集合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc

# 工作日工作时段（UTC）：09:00—17:00，每个工作日 8 小时
WORK_START_HOUR = 9
WORK_END_HOUR = 17

# 状态与合法转换：pending -> accept -> active；active 可 pause / submit；
# paused 仅可 resume；submitted 为终态。
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"accept"}),
    "active": frozenset({"pause", "submit"}),
    "paused": frozenset({"resume"}),
    "submitted": frozenset(),
}


@dataclass(frozen=True)
class ClockPair:
    """一对「稿件—评审人」及其正整数工作小时预算。"""

    manuscript_id: str
    reviewer_id: str
    budget_hours: int


@dataclass(frozen=True)
class ClockEvent:
    """一条配对状态事件（at 统一为时区感知 UTC 时刻）。"""

    manuscript_id: str
    reviewer_id: str
    type: str  # accept / pause / resume / submit
    at: datetime


@dataclass(frozen=True)
class ClockIssue:
    """一条拒绝原因（结构化）。"""

    type: str  # duplicate_pair / unknown_pair / duplicate_event / invalid_transition
    manuscript_id: str | None
    reviewer_id: str | None
    # 仅事件类问题携带：事件类型与事件 UTC 时刻
    event_type: str | None
    at: datetime | None
    detail: str


@dataclass(frozen=True)
class ClockResult:
    """一个配对在查询时刻的钟面快照。"""

    manuscript_id: str
    reviewer_id: str
    status: str  # pending / active / paused / submitted
    worked_seconds: int
    remaining_seconds: int
    # 预计截止 UTC 时刻；pending / paused 时为 None
    deadline: datetime | None
    overdue: bool


@dataclass(frozen=True)
class ClockReplay:
    results: tuple[ClockResult, ...]
    issues: tuple[ClockIssue, ...]


def _as_utc(t: datetime) -> datetime:
    """把时刻统一为时区感知 UTC；naive datetime 按 UTC 解释。"""
    if t.tzinfo is None:
        return t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def _window_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, WORK_START_HOUR, 0, tzinfo=UTC)


def _window_end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, WORK_END_HOUR, 0, tzinfo=UTC)


def _is_workday(d: date, holidays: frozenset[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def working_seconds_between(
    start: datetime, end: datetime, holidays: frozenset[date]
) -> int:
    """返回 [start, end] 与工作日历工作时段的重叠秒数（纯整数运算）。

    起止于非工作时刻（下班、周末、节假日）时只累计落在工作时段内的部分，
    跨下班、周末与节假日自然顺延；end <= start 时为 0。
    """
    start = _as_utc(start)
    end = _as_utc(end)
    if end <= start:
        return 0
    total = 0
    d = start.date()
    last = end.date()
    while d <= last:
        if _is_workday(d, holidays):
            ws = _window_start(d)
            we = _window_end(d)
            s = start if d == start.date() and start > ws else ws
            e = end if d == last and end < we else we
            if e > s:
                total += int((e - s).total_seconds())
        d += timedelta(days=1)
    return total


def advance_working(
    start: datetime, seconds: int, holidays: frozenset[date]
) -> datetime:
    """从 start 起在工作日历上累计 seconds 个工作秒后到达的 UTC 时刻。

    start 落在非工作时刻时先钳制到其后第一个工作瞬间；工作时间跨下班、
    周末与节假日自动顺延。seconds <= 0 时原样返回 start。
    """
    cur = _as_utc(start)
    remaining = int(seconds)
    if remaining <= 0:
        return cur
    d = cur.date()
    while True:
        if _is_workday(d, holidays):
            ws = _window_start(d)
            we = _window_end(d)
            if cur < ws:
                cur = ws
            if cur < we:
                avail = int((we - cur).total_seconds())
                if remaining <= avail:
                    return cur + timedelta(seconds=remaining)
                remaining -= avail
        d += timedelta(days=1)
        cur = _window_start(d)


def _issue_sort_key(issue: ClockIssue) -> tuple[str, str, str, str, str]:
    return (
        issue.type,
        issue.manuscript_id or "",
        issue.reviewer_id or "",
        issue.at.isoformat() if issue.at is not None else "",
        issue.event_type or "",
    )


def replay_clocks(
    pairs: list[ClockPair],
    events: list[ClockEvent],
    holidays: frozenset[date],
    query_at: datetime,
) -> ClockReplay:
    """按事件时间重放全部配对的状态机，返回查询时刻的钟面快照。

    事件按 (at, 输入下标) 稳定排序后只重放 at <= query_at 者；配对结果按
    (稿件 ID, 评审人 ID) 排序。存在任何校验问题时 issues 非空（确定性排序），
    此时调用方应整体拒绝且不使用 results。
    """
    query_at = _as_utc(query_at)
    issues: list[ClockIssue] = []

    # ---- 1. 配对去重：重复配对记一条 duplicate_pair ----
    budgets: dict[tuple[str, str], int] = {}
    for p in pairs:
        key = (p.manuscript_id, p.reviewer_id)
        if key in budgets:
            issues.append(ClockIssue(
                type="duplicate_pair",
                manuscript_id=p.manuscript_id,
                reviewer_id=p.reviewer_id,
                event_type=None,
                at=None,
                detail=(
                    f"配对 {p.manuscript_id} -> {p.reviewer_id} 在 pairs 中重复出现"
                ),
            ))
        else:
            budgets[key] = p.budget_hours

    # ---- 2. 事件按配对分组：保留输入下标，重放按 (时间, 下标) 稳定排序 ----
    # 仅重放 at <= query_at 的事件；查询时刻之后的事件整段忽略（既不改变
    # 状态也不参与校验），钟面反映查询时刻的快照。
    grouped: dict[tuple[str, str], list[tuple[datetime, int, ClockEvent]]] = {}
    for idx, e in enumerate(events):
        at = _as_utc(e.at)
        if at > query_at:
            continue
        grouped.setdefault((e.manuscript_id, e.reviewer_id), []).append((at, idx, e))

    # 未声明配对的事件只报 unknown_pair，不参与任何配对重放
    for key, raw in grouped.items():
        if key not in budgets:
            for at, _idx, e in sorted(raw, key=lambda x: (x[0], x[1])):
                issues.append(ClockIssue(
                    type="unknown_pair",
                    manuscript_id=e.manuscript_id,
                    reviewer_id=e.reviewer_id,
                    event_type=e.type,
                    at=at,
                    detail=(
                        f"事件引用了未在 pairs 中声明的配对 "
                        f"{e.manuscript_id} -> {e.reviewer_id}"
                    ),
                ))

    # ---- 3. 逐配对重放状态机 ----
    results: list[ClockResult] = []
    for key in sorted(budgets):
        mid, rid = key
        budget_seconds = budgets[key] * 3600
        raw = sorted(grouped.get(key, []), key=lambda x: (x[0], x[1]))

        status = "pending"
        active_since: datetime | None = None  # 当前计时段起点（active 时）
        worked_before = 0  # 当前计时段开始前已累计的工作秒数
        # 当前段投影出的预计截止时刻：accept/resume 时计算，pause 时清空
        projected_deadline: datetime | None = None
        submitted_at: datetime | None = None
        seen_ts: set[datetime] = set()

        for at, _idx, e in raw:
            # 同对同刻事件：无论类型是否相同一律拒绝，该事件不改变状态
            if at in seen_ts:
                issues.append(ClockIssue(
                    type="duplicate_event",
                    manuscript_id=mid,
                    reviewer_id=rid,
                    event_type=e.type,
                    at=at,
                    detail=(
                        f"配对 {mid} -> {rid} 在 {at.isoformat()} 存在重复事件"
                    ),
                ))
                continue
            seen_ts.add(at)

            if e.type not in LEGAL_TRANSITIONS[status]:
                allowed = "、".join(sorted(LEGAL_TRANSITIONS[status])) or "无（已提交）"
                issues.append(ClockIssue(
                    type="invalid_transition",
                    manuscript_id=mid,
                    reviewer_id=rid,
                    event_type=e.type,
                    at=at,
                    detail=(
                        f"配对 {mid} -> {rid} 在 {at.isoformat()} 的 {e.type} "
                        f"事件非法：当前状态为 {status}，仅允许：{allowed}"
                    ),
                ))
                continue  # 非法事件跳过，继续按其后事件重放

            if e.type == "accept":
                status = "active"
                active_since = at
                worked_before = 0
                projected_deadline = advance_working(at, budget_seconds, holidays)
            elif e.type == "pause":
                worked_before += working_seconds_between(active_since, at, holidays)
                status = "paused"
                active_since = None
                projected_deadline = None
            elif e.type == "resume":
                status = "active"
                active_since = at
                projected_deadline = advance_working(
                    at, budget_seconds - worked_before, holidays
                )
            elif e.type == "submit":
                worked_before += working_seconds_between(active_since, at, holidays)
                status = "submitted"
                submitted_at = at
                active_since = None

        # ---- 4. 查询时刻的钟面快照 ----
        worked = worked_before
        deadline: datetime | None = None
        overdue = False

        if status == "active":
            worked += working_seconds_between(active_since, query_at, holidays)
            deadline = projected_deadline
            overdue = query_at > deadline
        elif status == "submitted":
            # 工时与截止判定冻结在提交时刻；截止时刻为最后一段的投影
            worked = worked_before
            deadline = projected_deadline
            overdue = submitted_at > deadline

        results.append(ClockResult(
            manuscript_id=mid,
            reviewer_id=rid,
            status=status,
            worked_seconds=worked,
            remaining_seconds=budget_seconds - worked,
            deadline=deadline,
            overdue=overdue,
        ))

    issues.sort(key=_issue_sort_key)
    return ClockReplay(results=tuple(results), issues=tuple(issues))
