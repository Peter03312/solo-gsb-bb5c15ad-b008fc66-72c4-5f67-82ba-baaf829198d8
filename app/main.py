"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .clock import (
    ClockEvent as ClockEventSpec,
    ClockIssue,
    ClockPair as ClockPairSpec,
    replay_clocks,
)
from .models import (
    AssignmentIn,
    AssignmentOut,
    AssignRequest,
    AssignResponse,
    BottleneckCutEdgeOut,
    BottleneckCutOut,
    ClockIssueOut,
    ConflictAuditRequest,
    ConflictAuditResponse,
    CoverageAssignRequest,
    CoverageAssignResponse,
    CoverageConflictOut,
    DecisionRankingError,
    DecisionRankingRequest,
    DecisionRankingResponse,
    DiscussionScheduleError,
    DiscussionScheduleRequest,
    DiscussionScheduleResponse,
    IneligiblePairOut,
    LoadRatioOut,
    LockedConflictOut,
    ManuscriptAuditOut,
    ManuscriptRolesOut,
    PairSupportOut,
    ReviewClockError,
    ReviewClockPairOut,
    ReviewClockRequest,
    ReviewClockResponse,
    ReviewerShortfallOut,
    RankingIssueOut,
    RoleAssignRequest,
    RoleAssignResponse,
    RoleAssignmentOut,
    RoleUnsatisfiedOut,
    ScheduleIssueOut,
    ScheduledManuscriptOut,
    SharedPoolOut,
    UnsatisfiedOut,
    WorkloadBalanceOut,
)
from .audit import audit_conflicts
from .ranking import RankingBallot, schulze_rank
from .schedule import (
    DiscussionAssignment,
    DiscussionSlot,
    ReviewerAvailability,
    solve_discussion_schedule,
)
from .solver import (
    BottleneckCut,
    ManuscriptSpec,
    ReviewerSpec,
    solve_assignment,
    solve_coverage_assignment,
    solve_role_assignment,
)

app = FastAPI(
    title="Anonymous Review Assignment API",
    version="1.10.0",
    description=(
        "匿名评审分配服务：基于全局最大流（非逐稿贪心）求满足主题能力、"
        "利益冲突与容量约束的完整分配；不可行时返回缺口诊断与全局最小割证明"
        "（bottleneck_cut）且不输出部分结果。"
        "支持分赛道容量：稿件可声明 track，评审人可声明 track_capacities，"
        "赛道限额与总容量在同一流网络中联立求解。"
        "支持增量重排：携带 previous_assignments / locked_assignments 时，"
        "锁定关系经稿件需求、评审资格与容量校验后强制保留，其余名额在全局可行"
        "前提下最少改动地复用上一轮分配。"
        "支持负载均衡：balance_workload=true 时完整可行解按字典序优化——"
        "先最小化最大负载率（分配数/capacity，约分分数比较，不依赖浮点），"
        "再最小化相对上一轮的改动数，最后按 ID 破同。"
        "支持评审人下限覆盖（POST /assign-coverage）：评审人可声明 "
        "min_assignments（本轮至少分到的稿件数，缺省 0），接口在满足每稿所需"
        "人数的同时满足所有评审人的下限与上限，使用全局带下界流求完整可行解；"
        "下限大于容量、资格不足或共享容量冲突时返回 infeasible 并给出稿件侧 / "
        "评审人侧结构化诊断，不输出部分名单。"
        "支持分角色分配（POST /assign-roles）：稿件按角色声明所需人数、"
        "评审人声明可担任角色，全部稿件的全部角色在同一流网络中一次全局求解，"
        "同一评审人对同一稿件最多承担一个角色，所有角色共享总容量与赛道限额；"
        "可行时返回按稿件、角色分组的完整名单，不可行时返回各角色缺口。"
        "支持资格审计（POST /conflict-audit）：逐稿件、逐评审人判定资格，"
        "沿用自稿、显式回避与主题匹配规则；另接受人员 ID 组成的无向合作关系边"
        "与最大链长 max_hops（1～6），评审人到稿件任一作者存在不超过 max_hops "
        "跳的合作链时判为合作链利益冲突，返回按完整 ID 序列字典序选取的最短"
        "人员路径；审计只判定资格，不改动任何现有分配结果。"
        "支持评审工时钟重放（POST /review-clock）：对 /assign 名单形成的稿件—"
        "评审人配对，按 UTC 事件（接受、暂停、恢复、提交）重放状态机，只在"
        "周一至周五 09:00—17:00 UTC 且非节假日累计工作时长，返回各对状态、"
        "已用/剩余工时、预计截止时刻与逾期判定。"
        "支持共识排序（POST /decision-ranking）：接收 /assign 的可行分配名单"
        "与评审人对已评稿件的严格排序票，按 Schulze 最强路径法（仅成对胜者的"
        "直接支持可作为路径边，路径强度取沿途最小支持的最大值）生成稿件共识"
        "顺序，路径强度无法区分时按稿件 ID 破同，返回完整顺序与成对支持数。"
        "支持讨论时段排期（POST /schedule-discussions）：接收 /assign 可行结果中"
        "至多 8 篇稿件与评审人名单、至多 8 个候选时段、各评审人可出席时段与各时段"
        "容量，在每稿全体评审人共同可出席、同一评审人同时段只讨论一稿、时段容量"
        "不超出的全局约束下求完整排期；多方案按稿件 ID 顺序逐位比较时段 ID 取字典序"
        "最小者，无完整方案时返回 infeasible 且不输出部分排期，非法输入整体 422。"
        "服务无状态，不持久化任何稿件/评审信息。"
    ),
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "anonymous-review-assign",
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


def _to_pair_set(
    items: list[AssignmentIn] | None,
) -> frozenset[tuple[str, str]] | None:
    """把 AssignmentIn 列表展开为 (manuscript_id, reviewer_id) 对集合（自动去重）。"""
    if items is None:
        return None
    return frozenset(
        (a.manuscript_id, rid) for a in items for rid in set(a.reviewer_ids)
    )


def _to_bottleneck_cut_out(cut: BottleneckCut) -> BottleneckCutOut:
    def edges(group) -> list[BottleneckCutEdgeOut]:
        return [BottleneckCutEdgeOut(**vars(e)) for e in group]

    return BottleneckCutOut(
        source_manuscripts=list(cut.source_manuscripts),
        source_reviewer_tracks=[list(pair) for pair in cut.source_reviewer_tracks],
        source_reviewers=list(cut.source_reviewers),
        demand=edges(cut.demand),
        eligibility=edges(cut.eligibility),
        track_capacity=edges(cut.track_capacity),
        reviewer_capacity=edges(cut.reviewer_capacity),
        max_assignable=cut.max_assignable,
        shortfall=cut.shortfall,
    )


@app.post("/assign", response_model=AssignResponse, tags=["assign"])
def assign(req: AssignRequest) -> AssignResponse:
    default_required = req.required_reviewers
    manuscripts = [
        ManuscriptSpec(
            id=m.id,
            authors=tuple(m.authors),
            topics=tuple(m.topics),
            required_reviewers=(
                m.required_reviewers
                if m.required_reviewers is not None
                else default_required
            ),
            track=m.track,
        )
        for m in req.manuscripts
    ]
    reviewers = [
        ReviewerSpec(
            id=r.id,
            topics=tuple(r.topics),
            capacity=r.capacity,
            conflicted_authors=tuple(r.conflicted_authors),
            conflicted_manuscripts=tuple(r.conflicted_manuscripts),
            track_capacities=(
                tuple(sorted(r.track_capacities.items()))
                if r.track_capacities
                else ()
            ),
        )
        for r in req.reviewers
    ]

    result = solve_assignment(
        manuscripts,
        reviewers,
        previous=_to_pair_set(req.previous_assignments),
        locked=_to_pair_set(req.locked_assignments),
        balance_workload=req.balance_workload,
    )

    # 锁定关系冲突：结构化原因，不输出任何部分分配
    if result.locked_conflicts:
        return AssignResponse(
            status="infeasible",
            total_required=result.total_required,
            total_assigned=result.total_assigned,
            assignments=None,
            unsatisfied=None,
            shared_pool=None,
            locked_conflicts=[
                LockedConflictOut(**vars(c)) for c in result.locked_conflicts
            ],
        )

    if result.feasible:
        return AssignResponse(
            status="feasible",
            total_required=result.total_required,
            total_assigned=result.total_assigned,
            assignments=[
                AssignmentOut(manuscript_id=mid, reviewer_ids=list(rids))
                for mid, rids in sorted(result.assignments.items())
            ],
            unsatisfied=None,
            shared_pool=None,
            locked_conflicts=None,
            balance=(
                WorkloadBalanceOut(
                    max_load_ratio=LoadRatioOut(
                        numerator=result.balance.max_load_ratio_num,
                        denominator=result.balance.max_load_ratio_den,
                    ),
                    changes=result.balance.changes,
                )
                if result.balance
                else None
            ),
        )

    return AssignResponse(
        status="infeasible",
        total_required=result.total_required,
        total_assigned=result.total_assigned,
        assignments=None,
        unsatisfied=[UnsatisfiedOut(**vars(u)) for u in result.unsatisfied],
        shared_pool=SharedPoolOut(**result.shared_pool) if result.shared_pool else None,
        locked_conflicts=None,
        bottleneck_cut=(
            _to_bottleneck_cut_out(result.bottleneck_cut)
            if result.bottleneck_cut
            else None
        ),
    )


@app.post("/assign-coverage", response_model=CoverageAssignResponse, tags=["assign"])
def assign_coverage(req: CoverageAssignRequest) -> CoverageAssignResponse:
    default_required = req.required_reviewers
    manuscripts = [
        ManuscriptSpec(
            id=m.id,
            authors=tuple(m.authors),
            topics=tuple(m.topics),
            required_reviewers=(
                m.required_reviewers
                if m.required_reviewers is not None
                else default_required
            ),
            track=m.track,
        )
        for m in req.manuscripts
    ]
    reviewers = [
        ReviewerSpec(
            id=r.id,
            topics=tuple(r.topics),
            capacity=r.capacity,
            conflicted_authors=tuple(r.conflicted_authors),
            conflicted_manuscripts=tuple(r.conflicted_manuscripts),
            track_capacities=(
                tuple(sorted(r.track_capacities.items()))
                if r.track_capacities
                else ()
            ),
            min_assignments=r.min_assignments,
        )
        for r in req.reviewers
    ]

    result = solve_coverage_assignment(manuscripts, reviewers)

    # 结构性冲突：下限>容量 / 下限>个人可接上限，不输出任何部分名单
    if result.reviewer_conflicts:
        return CoverageAssignResponse(
            status="infeasible",
            total_required=result.total_required,
            total_min_required=result.total_min_required,
            total_assigned=result.total_assigned,
            assignments=None,
            reviewer_conflicts=[
                CoverageConflictOut(**vars(c)) for c in result.reviewer_conflicts
            ],
            unsatisfied=None,
            shared_pool=None,
            reviewer_shortfalls=None,
        )

    if result.feasible:
        return CoverageAssignResponse(
            status="feasible",
            total_required=result.total_required,
            total_min_required=result.total_min_required,
            total_assigned=result.total_assigned,
            assignments=[
                AssignmentOut(manuscript_id=mid, reviewer_ids=list(rids))
                for mid, rids in sorted(result.assignments.items())
            ],
            reviewer_conflicts=None,
            unsatisfied=None,
            shared_pool=None,
            reviewer_shortfalls=None,
        )

    return CoverageAssignResponse(
        status="infeasible",
        total_required=result.total_required,
        total_min_required=result.total_min_required,
        total_assigned=result.total_assigned,
        assignments=None,
        reviewer_conflicts=None,
        unsatisfied=(
            [UnsatisfiedOut(**vars(u)) for u in result.unsatisfied]
            if result.unsatisfied
            else None
        ),
        shared_pool=SharedPoolOut(**result.shared_pool) if result.shared_pool else None,
        reviewer_shortfalls=(
            [ReviewerShortfallOut(**vars(s)) for s in result.reviewer_shortfalls]
            if result.reviewer_shortfalls
            else None
        ),
    )


@app.post("/assign-roles", response_model=RoleAssignResponse, tags=["assign"])
def assign_roles(req: RoleAssignRequest) -> RoleAssignResponse:
    manuscripts = [
        ManuscriptSpec(
            id=m.id,
            authors=tuple(m.authors),
            topics=tuple(m.topics),
            required_reviewers=0,
            track=m.track,
            role_requirements=tuple(sorted(m.role_requirements.items())),
        )
        for m in req.manuscripts
    ]
    reviewers = [
        ReviewerSpec(
            id=r.id,
            topics=tuple(r.topics),
            capacity=r.capacity,
            conflicted_authors=tuple(r.conflicted_authors),
            conflicted_manuscripts=tuple(r.conflicted_manuscripts),
            track_capacities=(
                tuple(sorted(r.track_capacities.items()))
                if r.track_capacities
                else ()
            ),
            roles=tuple(sorted(set(r.roles))),
        )
        for r in req.reviewers
    ]

    result = solve_role_assignment(manuscripts, reviewers)

    if result.feasible:
        return RoleAssignResponse(
            status="feasible",
            total_required=result.total_required,
            total_assigned=result.total_assigned,
            assignments=[
                ManuscriptRolesOut(
                    manuscript_id=mid,
                    roles=[
                        RoleAssignmentOut(role=role, reviewer_ids=list(rids))
                        for role, rids in sorted(per_role.items())
                    ],
                )
                for mid, per_role in sorted(result.assignments.items())
            ],
            unsatisfied=None,
        )

    return RoleAssignResponse(
        status="infeasible",
        total_required=result.total_required,
        total_assigned=result.total_assigned,
        assignments=None,
        unsatisfied=[RoleUnsatisfiedOut(**vars(u)) for u in result.unsatisfied],
    )


@app.post("/conflict-audit", response_model=ConflictAuditResponse, tags=["audit"])
def conflict_audit(req: ConflictAuditRequest) -> ConflictAuditResponse:
    # 审计只读取资格相关字段（topics / authors / conflicted_*）；capacity、
    # track_capacities、required_reviewers 等分配字段不影响审计结果。
    manuscripts = [
        ManuscriptSpec(
            id=m.id,
            authors=tuple(m.authors),
            topics=tuple(m.topics),
            required_reviewers=(
                m.required_reviewers if m.required_reviewers is not None else 0
            ),
            track=m.track,
        )
        for m in req.manuscripts
    ]
    reviewers = [
        ReviewerSpec(
            id=r.id,
            topics=tuple(r.topics),
            capacity=r.capacity,
            conflicted_authors=tuple(r.conflicted_authors),
            conflicted_manuscripts=tuple(r.conflicted_manuscripts),
        )
        for r in req.reviewers
    ]
    # 无向边按 {端点排序} 去重：重复边、反向边与输入重排均不影响图本身
    edges = frozenset(
        (a, b) if a <= b else (b, a) for a, b in req.collaboration_edges
    )

    result = audit_conflicts(manuscripts, reviewers, edges, req.max_hops)

    return ConflictAuditResponse(
        max_hops=result.max_hops,
        audits=[
            ManuscriptAuditOut(
                manuscript_id=a.manuscript_id,
                eligible_reviewer_ids=list(a.eligible_reviewers),
                ineligible_pairs=[
                    IneligiblePairOut(
                        reviewer_id=p.reviewer_id,
                        reasons=list(p.reasons),
                        topic_mismatch=p.topic_mismatch,
                        collaboration_path=(
                            list(p.collaboration_path)
                            if p.collaboration_path is not None
                            else None
                        ),
                        hops=p.hops,
                    )
                    for p in a.ineligible_pairs
                ],
            )
            for a in result.audits
        ],
    )


def _hours(seconds: int) -> int | float:
    """整秒 -> 小时数：整小时返回 int，否则至多 6 位小数（5 秒精度）。"""
    if seconds % 3600 == 0:
        return seconds // 3600
    return round(seconds / 3600, 6)


def _clock_issue_out(issue: ClockIssue) -> ClockIssueOut:
    return ClockIssueOut(
        type=issue.type,
        manuscript_id=issue.manuscript_id,
        reviewer_id=issue.reviewer_id,
        event_type=issue.event_type,
        at=issue.at,
        detail=issue.detail,
    )


@app.post(
    "/review-clock",
    response_model=ReviewClockResponse,
    tags=["review-clock"],
    responses={422: {"model": ReviewClockError}},
)
def review_clock(req: ReviewClockRequest):
    holidays = frozenset(req.holidays)  # 重复日期自动去重
    pairs = [
        ClockPairSpec(
            manuscript_id=p.manuscript_id,
            reviewer_id=p.reviewer_id,
            budget_hours=p.budget_hours,
        )
        for p in req.pairs
    ]
    events = [
        ClockEventSpec(
            manuscript_id=e.manuscript_id,
            reviewer_id=e.reviewer_id,
            type=e.type,
            at=e.at,
        )
        for e in req.events
    ]

    replay = replay_clocks(pairs, events, holidays, req.query_at)
    if replay.issues:
        # 重复配对 / 未知配对 / 同对同刻事件 / 非法状态转换：整体拒绝，不输出钟结果
        return JSONResponse(
            status_code=422,
            content=ReviewClockError(
                errors=[_clock_issue_out(i) for i in replay.issues]
            ).model_dump(mode="json"),
        )

    return ReviewClockResponse(
        query_at=req.query_at,
        holidays=sorted(holidays),
        clocks=[
            ReviewClockPairOut(
                manuscript_id=c.manuscript_id,
                reviewer_id=c.reviewer_id,
                status=c.status,
                worked_hours=_hours(c.worked_seconds),
                remaining_hours=_hours(c.remaining_seconds),
                deadline=c.deadline,
                overdue=c.overdue,
            )
            for c in replay.results
        ],
    )


@app.post(
    "/decision-ranking",
    response_model=DecisionRankingResponse,
    tags=["decision-ranking"],
    responses={422: {"model": DecisionRankingError}},
)
def decision_ranking(req: DecisionRankingRequest):
    # /assign 名单：稿件 -> 评审人集合。注意不能复用 _to_pair_set（后者展开为
    # (稿件, 评审人) 对集合，会把 reviewer_ids 为空列表的稿件整条丢掉）；本接口
    # 必须保留「无人可评」的稿件——它仍参与共识顺序。条目与评审人重复自动去重。
    assignments = [
        (a.manuscript_id, list(set(a.reviewer_ids))) for a in req.assignments
    ]
    ballots = [
        RankingBallot(reviewer_id=b.reviewer_id, ranking=tuple(b.ranking))
        for b in req.ballots
    ]

    result = schulze_rank(assignments, ballots)
    if result.issues:
        # 每人多票 / 票内重复 / 稿件不属于其分配名单：整体拒绝，不输出任何排序结果
        return JSONResponse(
            status_code=422,
            content=DecisionRankingError(
                errors=[
                    RankingIssueOut(
                        type=i.type,
                        reviewer_id=i.reviewer_id,
                        manuscript_id=i.manuscript_id,
                        detail=i.detail,
                    )
                    for i in result.issues
                ]
            ).model_dump(mode="json"),
        )

    return DecisionRankingResponse(
        ranking=list(result.order),
        pair_support=[
            PairSupportOut(
                manuscript_id_a=p.manuscript_id_a,
                manuscript_id_b=p.manuscript_id_b,
                support=p.support,
            )
            for p in result.pair_support
        ],
    )


@app.post(
    "/schedule-discussions",
    response_model=DiscussionScheduleResponse,
    tags=["discussion-schedule"],
    responses={422: {"model": DiscussionScheduleError}},
)
def schedule_discussions(req: DiscussionScheduleRequest):
    assignments = [
        DiscussionAssignment(
            manuscript_id=a.manuscript_id,
            reviewer_ids=tuple(a.reviewer_ids),
        )
        for a in req.assignments
    ]
    slots = [
        DiscussionSlot(id=s.id, capacity=s.capacity)
        for s in req.slots
    ]
    availability = [
        ReviewerAvailability(
            reviewer_id=av.reviewer_id,
            slot_ids=tuple(av.slot_ids),
        )
        for av in req.reviewer_availability
    ]

    result = solve_discussion_schedule(assignments, slots, availability)
    if result.issues:
        # 超数量上限 / 重复 ID / 评审人未声明可用时段 / 引用未知时段：
        # 整体拒绝，不输出任何排期
        return JSONResponse(
            status_code=422,
            content=DiscussionScheduleError(
                errors=[
                    ScheduleIssueOut(
                        type=i.type,
                        manuscript_id=i.manuscript_id,
                        slot_id=i.slot_id,
                        reviewer_id=i.reviewer_id,
                        detail=i.detail,
                    )
                    for i in result.issues
                ]
            ).model_dump(mode="json"),
        )

    if result.schedule or not assignments:
        return DiscussionScheduleResponse(
            status="feasible",
            schedule=[
                ScheduledManuscriptOut(manuscript_id=mid, slot_id=sid)
                for mid, sid in result.schedule
            ],
        )

    # 输入合法但无完整方案：不输出部分排期
    return DiscussionScheduleResponse(status="infeasible", schedule=None)
