"""请求 / 响应模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_TrackName = Annotated[str, Field(min_length=1, max_length=128)]
_TrackCapacity = Annotated[int, Field(ge=0, le=1000)]


class ManuscriptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="稿件 ID（唯一）")
    authors: list[str] = Field(default_factory=list, max_length=512, description="作者名单")
    topics: list[str] = Field(default_factory=list, max_length=64, description="稿件主题")
    required_reviewers: int | None = Field(
        default=None, ge=0, le=100,
        description="该稿所需评审数；缺省时使用请求级 required_reviewers",
    )
    track: str | None = Field(
        default=None, min_length=1, max_length=128,
        description="稿件所属赛道；缺省表示不参与分赛道容量控制",
    )


class ReviewerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="评审人 ID（唯一）")
    topics: list[str] = Field(default_factory=list, max_length=64, description="主题能力")
    capacity: int = Field(ge=0, le=1000, description="最多可评审稿件数")
    conflicted_authors: list[str] = Field(
        default_factory=list, max_length=2048, description="存在利益冲突的作者名单"
    )
    conflicted_manuscripts: list[str] = Field(
        default_factory=list, max_length=2048, description="需回避的稿件 ID 列表"
    )
    track_capacities: dict[_TrackName, _TrackCapacity] | None = Field(
        default=None, max_length=64,
        description=(
            "分赛道容量：赛道 -> 该赛道最多可评审的稿件数（非负整数）。"
            "映射中出现的赛道同时受赛道限额与总容量约束；"
            "未列出的赛道仅受总容量约束"
        ),
    )


class AssignmentIn(BaseModel):
    """一组「稿件 -> 评审人」关系（previous / locked 通用）。"""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1, max_length=128, description="稿件 ID")
    reviewer_ids: list[str] = Field(
        min_length=1, max_length=10000,
        description="分配给该稿件的评审人 ID 列表（重复项自动去重）",
    )


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manuscripts: list[ManuscriptIn] = Field(max_length=10000)
    reviewers: list[ReviewerIn] = Field(max_length=10000)
    required_reviewers: int = Field(
        default=1, ge=0, le=100,
        description="每稿默认所需评审数（稿件级 required_reviewers 可覆盖）",
    )
    previous_assignments: list[AssignmentIn] | None = Field(
        default=None, max_length=10000,
        description=(
            "上一轮分配结果（增量重排模式）。提供后，求解在保证全局可行的前提下"
            "尽量复用这些关系，使新增 + 移除的关系总数最少；引用已不存在的"
            "稿件/评审人的关系自动忽略"
        ),
    )
    locked_assignments: list[AssignmentIn] | None = Field(
        default=None, max_length=10000,
        description=(
            "必须保留的锁定分配（增量重排模式）。任一锁定关系不满足稿件需求、"
            "评审资格或容量约束时，整体判定不可行并返回结构化冲突原因"
        ),
    )
    balance_workload: bool = Field(
        default=False,
        description=(
            "启用负载均衡优化：完整可行解在资格、赛道、锁定与容量约束下按字典序"
            "优化——先最小化所有 capacity>0 评审人最终负载率（分配数/capacity）"
            "的最大值，再最小化相对 previous_assignments 的新增+移除关系总数，"
            "同成本解按 ID 确定性破除；负载率以约分分数（整数分子/分母）表示与"
            "比较，不依赖浮点"
        ),
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "AssignRequest":
        m_ids = [m.id for m in self.manuscripts]
        if len(m_ids) != len(set(m_ids)):
            raise ValueError("manuscripts 中存在重复的 id")
        r_ids = [r.id for r in self.reviewers]
        if len(r_ids) != len(set(r_ids)):
            raise ValueError("reviewers 中存在重复的 id")
        return self


class AssignmentOut(BaseModel):
    manuscript_id: str
    reviewer_ids: list[str]


class UnsatisfiedOut(BaseModel):
    manuscript_id: str
    required: int
    assigned: int
    missing: int
    eligible_reviewers: int
    eligible_remaining_capacity: int
    candidate_gap: int
    capacity_gap: int


class SharedPoolOut(BaseModel):
    manuscript_ids: list[str]
    reviewer_ids: list[str]
    missing_total: int
    pool_remaining_capacity: int
    pool_total_capacity: int


class LockedConflictOut(BaseModel):
    """一条锁定关系冲突的结构化原因。"""

    type: Literal[
        "unknown_manuscript",
        "unknown_reviewer",
        "self_conflict",
        "author_conflict",
        "manuscript_conflict",
        "topic_mismatch",
        "reviewer_capacity_exceeded",
        "reviewer_track_capacity_exceeded",
        "manuscript_requirement_exceeded",
    ]
    manuscript_id: str | None = None
    reviewer_id: str | None = None
    # 仅容量/需求类冲突携带：实际锁定数与上限
    locked: int | None = None
    limit: int | None = None
    # 仅分赛道容量冲突携带：被超出限额的赛道
    track: str | None = None
    detail: str


class BottleneckCutEdgeOut(BaseModel):
    """一条跨越瓶颈割（源侧 -> 汇侧）的饱和边。"""

    # demand：源 -> 稿件（稿件需求）；eligibility：稿件 -> 评审人/赛道节点（评审资格）；
    # track_capacity：(评审人,赛道) -> 评审人（赛道限额）；
    # reviewer_capacity：评审人 -> 汇（评审总容量）
    type: Literal[
        "demand",
        "eligibility",
        "track_capacity",
        "reviewer_capacity",
    ]
    # 建图容量（增量模式下为扣除合法锁定占用后的剩余容量）
    capacity: int
    manuscript_id: str | None = None
    reviewer_id: str | None = None
    # eligibility 走赛道节点 / track_capacity 边时携带赛道名
    track: str | None = None


class BottleneckCutOut(BaseModel):
    """不可行的全局最小割证明（求解结束的残量网络上，割容量 = 最大流）。"""

    # 残量网络中从源点可达（源侧）的业务节点，按业务 ID 排序
    source_manuscripts: list[str]
    # (评审人 ID, 赛道名) 二元组，按业务 ID 排序
    source_reviewer_tracks: list[list[str]]
    source_reviewers: list[str]
    # 跨割饱和边，按类型分组、组内按业务 ID 确定排序
    demand: list[BottleneckCutEdgeOut]
    eligibility: list[BottleneckCutEdgeOut]
    track_capacity: list[BottleneckCutEdgeOut]
    reviewer_capacity: list[BottleneckCutEdgeOut]
    # 割容量：全部约束下至多可满足的名额数（增量模式含已固化的合法锁定名额）
    max_assignable: int
    # 总需求 - max_assignable
    shortfall: int


class LoadRatioOut(BaseModel):
    """约分后的分数（分子 / 分母互质，分母为正）。"""

    numerator: int
    denominator: int


class WorkloadBalanceOut(BaseModel):
    """balance_workload 启用且可行时的负载均衡结果。"""

    # 所有 capacity>0 评审人最终负载率（分配数/capacity）最大值的约分分数
    max_load_ratio: LoadRatioOut
    # 相对 previous_assignments 的新增 + 移除关系总数
    changes: int


class AssignResponse(BaseModel):
    status: Literal["feasible", "infeasible"]
    total_required: int
    total_assigned: int
    # 可行时给出完整分配；不可行时为 null（不输出部分结果）
    assignments: list[AssignmentOut] | None
    # 不可行时给出缺口诊断；可行时为 null
    unsatisfied: list[UnsatisfiedOut] | None
    shared_pool: SharedPoolOut | None
    # 锁定关系冲突（仅增量重排模式且锁定不可行时非空）；否则为 null
    locked_conflicts: list[LockedConflictOut] | None = None
    # 合法但剩余需求无法满足时的全局最小割证明；可行或锁定校验失败时为 null
    bottleneck_cut: BottleneckCutOut | None = None
    # balance_workload 启用且可行时的负载均衡结果；否则为 null
    balance: WorkloadBalanceOut | None = None


# ---------- POST /assign-coverage：评审人下限覆盖 ----------


class CoverageReviewerIn(ReviewerIn):
    """评审人字段与 /assign 相同，另可声明本轮至少分到的稿件数。"""

    min_assignments: int = Field(
        default=0, ge=0, le=1000,
        description="本轮至少分到的稿件数（下界）；缺省为 0",
    )


class CoverageAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manuscripts: list[ManuscriptIn] = Field(max_length=10000)
    reviewers: list[CoverageReviewerIn] = Field(max_length=10000)
    required_reviewers: int = Field(
        default=1, ge=0, le=100,
        description="每稿默认所需评审数（稿件级 required_reviewers 可覆盖）",
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "CoverageAssignRequest":
        m_ids = [m.id for m in self.manuscripts]
        if len(m_ids) != len(set(m_ids)):
            raise ValueError("manuscripts 中存在重复的 id")
        r_ids = [r.id for r in self.reviewers]
        if len(r_ids) != len(set(r_ids)):
            raise ValueError("reviewers 中存在重复的 id")
        return self


class CoverageConflictOut(BaseModel):
    """评审人下限的结构性冲突（不需求解即可判定整体不可行）。"""

    type: Literal["min_exceeds_capacity", "min_exceeds_eligible"]
    reviewer_id: str
    min_assignments: int
    # 冲突上限：容量（min_exceeds_capacity）或个人可接上限（min_exceeds_eligible）
    limit: int
    detail: str


class ReviewerShortfallOut(BaseModel):
    """评审人侧诊断：全局最优努力下仍达不到下限的评审人。"""

    reviewer_id: str
    min_assignments: int
    # 最优努力下实际能分到的稿件数
    attained: int
    missing: int


class CoverageAssignResponse(BaseModel):
    status: Literal["feasible", "infeasible"]
    total_required: int
    # 全部评审人声明的下限之和
    total_min_required: int
    # 可行时等于 total_required；不可行时恒为 0（不输出任何部分名单）
    total_assigned: int
    # 可行时给出完整分配；不可行时为 null（不输出部分名单）
    assignments: list[AssignmentOut] | None
    # 结构性冲突（下限>容量 / 下限>个人可接上限）时非空；否则为 null
    reviewer_conflicts: list[CoverageConflictOut] | None
    # 稿件侧缺口诊断（忽略下限仍无法完整覆盖时非空）；否则为 null
    unsatisfied: list[UnsatisfiedOut] | None
    shared_pool: SharedPoolOut | None
    # 评审人侧下限缺口诊断（凑不够下限时非空）；否则为 null
    reviewer_shortfalls: list[ReviewerShortfallOut] | None


# ---------- POST /assign-roles：分角色分配 ----------

_RoleName = Annotated[str, Field(min_length=1, max_length=128)]
_RoleRequired = Annotated[int, Field(ge=0, le=100)]


class RoleManuscriptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="稿件 ID（唯一）")
    authors: list[str] = Field(default_factory=list, max_length=512, description="作者名单")
    topics: list[str] = Field(default_factory=list, max_length=64, description="稿件主题")
    role_requirements: dict[_RoleName, _RoleRequired] = Field(
        max_length=64,
        description="各评审角色所需人数：角色 -> 非负整数人数（0 表示该角色无需评审）",
    )
    track: str | None = Field(
        default=None, min_length=1, max_length=128,
        description="稿件所属赛道；缺省表示不参与分赛道容量控制",
    )


class RoleReviewerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="评审人 ID（唯一）")
    topics: list[str] = Field(default_factory=list, max_length=64, description="主题能力")
    capacity: int = Field(
        ge=0, le=1000,
        description="最多可承担的评审任务数（不分角色、不分稿件，合计）",
    )
    roles: list[_RoleName] = Field(
        default_factory=list, max_length=64,
        description="可担任的评审角色列表（重复项自动去重）；缺省表示不能担任任何角色",
    )
    conflicted_authors: list[str] = Field(
        default_factory=list, max_length=2048, description="存在利益冲突的作者名单"
    )
    conflicted_manuscripts: list[str] = Field(
        default_factory=list, max_length=2048, description="需回避的稿件 ID 列表"
    )
    track_capacities: dict[_TrackName, _TrackCapacity] | None = Field(
        default=None, max_length=64,
        description=(
            "分赛道容量：赛道 -> 该赛道最多可评审的稿件数（非负整数）。"
            "映射中出现的赛道同时受赛道限额与总容量约束；"
            "未列出的赛道仅受总容量约束"
        ),
    )


class RoleAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manuscripts: list[RoleManuscriptIn] = Field(max_length=10000)
    reviewers: list[RoleReviewerIn] = Field(max_length=10000)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "RoleAssignRequest":
        m_ids = [m.id for m in self.manuscripts]
        if len(m_ids) != len(set(m_ids)):
            raise ValueError("manuscripts 中存在重复的 id")
        r_ids = [r.id for r in self.reviewers]
        if len(r_ids) != len(set(r_ids)):
            raise ValueError("reviewers 中存在重复的 id")
        return self


class RoleAssignmentOut(BaseModel):
    """某稿件某角色分得的评审人名单（按 ID 排序）。"""

    role: str
    reviewer_ids: list[str]


class ManuscriptRolesOut(BaseModel):
    """某稿件的完整分角色名单（角色按名称排序）。"""

    manuscript_id: str
    roles: list[RoleAssignmentOut]


class RoleUnsatisfiedOut(BaseModel):
    """不可行时某稿件某角色的缺口诊断。"""

    manuscript_id: str
    role: str
    required: int
    assigned: int
    missing: int
    eligible_reviewers: int
    eligible_remaining_capacity: int


class RoleAssignResponse(BaseModel):
    status: Literal["feasible", "infeasible"]
    total_required: int
    total_assigned: int
    # 可行时给出按稿件、角色分组的完整名单；不可行时为 null（不输出部分结果）
    assignments: list[ManuscriptRolesOut] | None
    # 不可行时给出各角色缺口诊断；可行时为 null
    unsatisfied: list[RoleUnsatisfiedOut] | None


# ---------- POST /conflict-audit：资格审计（含合作链利益冲突） ----------

_PersonId = Annotated[str, Field(min_length=1, max_length=128)]


class ConflictAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manuscripts: list[ManuscriptIn] = Field(max_length=10000)
    # capacity / track_capacities 等分配字段可照常携带，审计只读取资格相关字段
    reviewers: list[ReviewerIn] = Field(max_length=10000)
    collaboration_edges: list[tuple[_PersonId, _PersonId]] = Field(
        default_factory=list,
        max_length=100000,
        description=(
            "人员 ID 组成的无向合作关系边 [人员A, 人员B]；边无方向、重复边自动去重，"
            "边中人员不必是稿件作者或评审人（可仅作为合作链中间节点）"
        ),
    )
    max_hops: int = Field(
        ge=1, le=6,
        description="合作链利益冲突的最大跳数（1～6）：评审人到稿件任一作者的"
                    "最短合作链不超过该跳数即判利益冲突",
    )

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "ConflictAuditRequest":
        m_ids = [m.id for m in self.manuscripts]
        if len(m_ids) != len(set(m_ids)):
            raise ValueError("manuscripts 中存在重复的 id")
        r_ids = [r.id for r in self.reviewers]
        if len(r_ids) != len(set(r_ids)):
            raise ValueError("reviewers 中存在重复的 id")
        return self


# 利益冲突原因（主题不匹配不是利益冲突，单独以 topic_mismatch 标记）：
#   self_conflict                —— 评审人是稿件作者（自稿冲突）
#   author_conflict              —— 评审人显式声明与稿件作者冲突
#   manuscript_conflict          —— 评审人显式声明回避该稿件
#   collaboration_chain_conflict —— 评审人经不超过 max_hops 跳的合作链关联到稿件作者
ConflictReason = Literal[
    "self_conflict",
    "author_conflict",
    "manuscript_conflict",
    "collaboration_chain_conflict",
]


class IneligiblePairOut(BaseModel):
    """一个不合格（稿件, 评审人）配对的全部原因。"""

    reviewer_id: str
    # 利益冲突原因（可多个并存，固定顺序）；仅主题不匹配时为空列表
    reasons: list[ConflictReason]
    # 主题不匹配单独标记（可与任一利益冲突原因并存）
    topic_mismatch: bool
    # 仅 collaboration_chain_conflict 时给出最短人员路径 [评审人, ..., 作者]
    collaboration_path: list[str] | None = None
    # 最短合作链跳数（= len(collaboration_path) - 1）；无合作链冲突时为 null
    hops: int | None = None


class ManuscriptAuditOut(BaseModel):
    """某稿件对全部评审人的资格审计结果。"""

    manuscript_id: str
    # 合格评审人 ID（无任何利益冲突原因且主题匹配），按 ID 排序
    eligible_reviewer_ids: list[str]
    # 不合格配对，按评审人 ID 排序
    ineligible_pairs: list[IneligiblePairOut]


class ConflictAuditResponse(BaseModel):
    max_hops: int
    # 按稿件 ID 排序
    audits: list[ManuscriptAuditOut]


# ---------- POST /review-clock：评审工时钟重放 ----------

# 工时钟事件：接受 / 暂停 / 恢复 / 提交
ClockEventType = Literal["accept", "pause", "resume", "submit"]
# 工时钟状态：待接受 / 计时中 / 已暂停 / 已提交
ClockStatus = Literal["pending", "active", "paused", "submitted"]
ClockIssueType = Literal[
    "duplicate_pair",
    "unknown_pair",
    "duplicate_event",
    "invalid_transition",
]
# 工时（小时）：整小时为整数，不足整小时给出至多 6 位小数（始终是 5 秒的倍数）
WorkHours = int | float


class ReviewClockPairIn(BaseModel):
    """一对由 /assign 名单形成的「稿件—评审人」配对及其工作小时预算。"""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1, max_length=128, description="稿件 ID")
    reviewer_id: str = Field(min_length=1, max_length=128, description="评审人 ID")
    budget_hours: int = Field(
        ge=1, le=100000,
        description="该对评审工作的正整数小时预算（仅工作时段累计）",
    )


class ReviewClockEventIn(BaseModel):
    """一条配对状态事件（UTC 时刻）。"""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1, max_length=128, description="稿件 ID")
    reviewer_id: str = Field(min_length=1, max_length=128, description="评审人 ID")
    type: ClockEventType = Field(
        description="事件类型：accept（接受）/ pause（暂停）/ resume（恢复）/ submit（提交）",
    )
    at: datetime = Field(
        description=(
            "事件发生的 UTC 时刻（建议以 Z 结尾的 ISO 8601；"
            "不带时区偏移的日期时间按 UTC 解释）"
        ),
    )


class ReviewClockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[ReviewClockPairIn] = Field(
        min_length=1, max_length=10000,
        description=(
            "由 /assign 名单形成的稿件—评审人配对及每对正整数工作小时预算；"
            "同一配对重复出现将被拒绝（duplicate_pair）"
        ),
    )
    events: list[ReviewClockEventIn] = Field(
        default_factory=list, max_length=100000,
        description=(
            "配对状态事件列表（accept / pause / resume / submit），按各事件 "
            "at 时间重放；晚于 query_at 的事件整段忽略"
        ),
    )
    holidays: list[date] = Field(
        default_factory=list, max_length=3660,
        description=(
            "节假日日期列表（整日不计工作时间，按 UTC 日期；重复日期自动去重）"
        ),
    )
    query_at: datetime = Field(
        description=(
            "查询时刻（UTC；建议以 Z 结尾的 ISO 8601，naive 日期时间按 UTC 解释）："
            "仅重放不晚于该时刻的事件，钟面结果为该时刻的快照"
        ),
    )


class ClockIssueOut(BaseModel):
    """一条拒绝原因（整体 422 时返回全部问题，不输出任何钟结果）。"""

    type: ClockIssueType
    manuscript_id: str | None = None
    reviewer_id: str | None = None
    # 仅事件类问题（duplicate_event / invalid_transition / unknown_pair）携带
    event_type: ClockEventType | None = None
    at: datetime | None = None
    detail: str


class ReviewClockPairOut(BaseModel):
    """一个配对在查询时刻的钟面快照（按稿件、评审人 ID 排序）。"""

    manuscript_id: str
    reviewer_id: str
    status: ClockStatus
    # 已累计的工作小时数（仅工作时段；整小时为整数，否则至多 6 位小数）
    worked_hours: WorkHours
    # 剩余工作小时数（预算 - 已用；提交逾期时可能为负）
    remaining_hours: WorkHours
    # 预计截止 UTC 时刻；pending / paused 时为 null
    deadline: datetime | None
    # 是否逾期：计时中按查询时刻 vs 截止时刻；提交后按提交时刻判定
    overdue: bool


class ReviewClockResponse(BaseModel):
    query_at: datetime
    holidays: list[date]
    # 按 (manuscript_id, reviewer_id) 排序
    clocks: list[ReviewClockPairOut]


class ReviewClockError(BaseModel):
    """请求被拒绝（422）：结构化问题清单，clocks 不输出。"""

    errors: list[ClockIssueOut]


# ---------- POST /decision-ranking：Schulze 共识排序 ----------

DecisionRankingIssueType = Literal[
    "duplicate_ballot",
    "duplicate_manuscript",
    "manuscript_not_assigned",
]


class RankingAssignmentIn(BaseModel):
    """一条「稿件 -> 评审人」可行分配关系（取自 /assign 响应，重复项自动去重）。"""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1, max_length=128, description="稿件 ID")
    reviewer_ids: list[str] = Field(
        max_length=10000,
        description=(
            "该稿件的评审人 ID 列表（取自 /assign 的可行分配，重复项自动去重）；"
            "可为空列表，表示该稿本轮无人可评"
        ),
    )


class RankingBallotIn(BaseModel):
    """一位评审人对其已评稿件的严格排序票（最前 -> 最后）。"""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=128, description="投票评审人 ID")
    ranking: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=10000,
        description=(
            "按严格偏好从最前到最后排列的稿件 ID 列表：允许只排部分稿件；"
            "票内稿件不得重复，且每一篇都必须属于该评审人的分配名单。"
            "两稿仅在同一张票中同时出现时才计入成对支持；空列表等于不投票"
        ),
    )


class DecisionRankingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[RankingAssignmentIn] = Field(
        max_length=10000,
        description=(
            "/assign 可行分配名单（稿件 -> 评审人列表）；条目与评审人重复自动去重。"
            "允许为空列表（此时共识顺序与成对支持均为空）"
        ),
    )
    ballots: list[RankingBallotIn] = Field(
        default_factory=list, max_length=10000,
        description=(
            "评审人严格排序票列表；每位评审人最多一票，票内稿件不得重复且必须"
            "属于其分配名单，缺省为空列表（无票时共识顺序完全由稿件 ID 破同）"
        ),
    )


class RankingIssueOut(BaseModel):
    """一条非法投票原因（整体 422 时返回全部问题，不输出任何排序结果）。"""

    type: DecisionRankingIssueType
    reviewer_id: str
    # duplicate_ballot 时为 null；其余携带问题稿件 ID
    manuscript_id: str | None = None
    detail: str


class DecisionRankingError(BaseModel):
    """请求被拒绝（422）：结构化问题清单，不输出共识顺序。"""

    errors: list[RankingIssueOut]


class PairSupportOut(BaseModel):
    """一个有序稿件对的直接成对支持：将 manuscript_id_a 排在 manuscript_id_b 前的票数。"""

    manuscript_id_a: str
    manuscript_id_b: str
    support: int


class DecisionRankingResponse(BaseModel):
    # 共识顺序（第一名 -> 最后一名），覆盖分配名单中的全部稿件
    ranking: list[str]
    # 全部有序稿件对 (a != b) 的直接成对支持，按 (manuscript_id_a, manuscript_id_b) 排序
    pair_support: list[PairSupportOut]


# ---------- POST /schedule-discussions：讨论时段排期 ----------

# 数量上限与 app.schedule 中的求解器常量保持一致
_MAX_DISCUSSION_MANUSCRIPTS = 8
_MAX_DISCUSSION_SLOTS = 8


class ScheduleAssignmentIn(BaseModel):
    """一条「稿件 -> 评审人」可行分配关系（取自 /assign 的可行结果）。"""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1, max_length=128, description="稿件 ID")
    reviewer_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=10000,
        description=(
            "该稿件的评审人 ID 列表（取自 /assign 的可行分配，名单内重复自动去重）；"
            "可为空列表，表示该稿无评审人（不受共同出席约束）"
        ),
    )


class DiscussionSlotIn(BaseModel):
    """一个候选讨论时段及其非负整数稿件容量。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="时段 ID（唯一）")
    capacity: int = Field(
        ge=0, le=1000,
        description="该时段最多可讨论的稿件数（非负整数；0 表示该时段不可排稿）",
    )


class ReviewerAvailabilityIn(BaseModel):
    """一位评审人可出席的候选时段列表。"""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=128, description="评审人 ID")
    slot_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=10000,
        description=(
            "该评审人可出席的时段 ID 列表（重复项自动去重）；可为空列表，"
            "表示该评审人没有任何可出席的时段"
        ),
    )


class DiscussionScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[ScheduleAssignmentIn] = Field(
        max_length=10000,
        description=(
            "/assign 可行分配结果中待排入讨论时段的稿件（至多 8 篇）及其评审人名单；"
            "稿件 ID 不得重复"
        ),
    )
    slots: list[DiscussionSlotIn] = Field(
        max_length=10000,
        description=(
            "候选讨论时段列表（至多 8 个，可为空）；时段 ID 不得重复。"
            "列表为空且有待排稿件时判定不可行"
        ),
    )
    reviewer_availability: list[ReviewerAvailabilityIn] = Field(
        default_factory=list, max_length=10000,
        description=(
            "每位评审人可出席的时段列表；同一评审人至多一条记录，"
            "分配名单中出现的每位评审人都必须声明，引用的时段必须已在 slots 中定义"
        ),
    )


class ScheduledManuscriptOut(BaseModel):
    """一篇稿件的排期结果。"""

    manuscript_id: str
    slot_id: str


class DiscussionScheduleResponse(BaseModel):
    # feasible 时给出完整排期（每篇稿件恰好一个时段，按稿件 ID 排序）；
    # infeasible 时为 null（不输出部分排期）
    status: Literal["feasible", "infeasible"]
    schedule: list[ScheduledManuscriptOut] | None


ScheduleIssueType = Literal[
    "too_many_manuscripts",
    "too_many_slots",
    "duplicate_manuscript",
    "duplicate_slot",
    "duplicate_availability",
    "reviewer_availability_undeclared",
    "unknown_slot_reference",
]


class ScheduleIssueOut(BaseModel):
    """一条非法输入原因（整体 422 时返回全部问题，不输出任何排期）。"""

    type: ScheduleIssueType
    # 不适用的维度为 null
    manuscript_id: str | None = None
    slot_id: str | None = None
    reviewer_id: str | None = None
    detail: str


class DiscussionScheduleError(BaseModel):
    """请求被拒绝（422）：结构化问题清单，不输出任何排期。"""

    errors: list[ScheduleIssueOut]
