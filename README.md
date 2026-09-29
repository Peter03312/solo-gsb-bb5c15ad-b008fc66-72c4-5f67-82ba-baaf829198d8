# 匿名评审分配 API（Anonymous Review Assignment）

基于 Python + FastAPI 的评审分配服务。`POST /assign` 将「稿件—评审人」分配问题整体规约为
**全局最大流**（Edmonds-Karp，非逐稿贪心），一次求出同时满足以下全部约束的完整分配：

- 每篇稿件获得指定数量的评审人；
- 评审人主题能力与稿件主题匹配；
- 回避利益冲突（含自稿冲突）；
- 评审人分配总量不超过其容量；
- 评审人声明了某赛道限额时，其在该赛道上的分配量不超过对应限额
  （赛道限额与总容量在同一流网络中联立求解，而非分赛道独立求解后拼接）。

无可行解时返回未满足稿件及候选/容量缺口诊断，并在求解结束的残量网络上给出
**全局最小割证明**（`bottleneck_cut`：源侧节点 + 按 `demand` / `eligibility` /
`track_capacity` / `reviewer_capacity` 分类的跨割饱和边 + `max_assignable` 与
`shortfall`），**不输出任何部分分配结果**；
相同输入（甚至不同的列表顺序）必然产生完全相同的输出。服务无状态，不持久化任何数据。

请求携带 `previous_assignments` / `locked_assignments` 时进入**增量重排模式**：
锁定关系经稿件需求、评审资格与容量校验后强制保留（任一冲突即整体不可行并返回结构化原因），
其余名额在保证全局可行的前提下以**最小费用最大流**求解，使相对上一轮的
**新增 + 移除关系总数最少**，同成本解按 ID 顺序确定性破除。

请求携带 `"balance_workload": true` 时进入**负载均衡模式**：完整可行解按字典序优化——
先最小化所有 `capacity > 0` 评审人**最终负载率**（`分配数 / capacity`）的最大值，
再最小化相对 `previous_assignments` 的新增 + 移除总数，最后按 ID 破同；
负载率以**约分分数**表示与比较（不依赖浮点），响应返回最优最大负载率的约分分数
与改动数；无完整解时沿用现有诊断，不输出部分分配。

请求携带 `"balance_workload": true` 时进入**负载均衡模式**：完整可行解在资格、赛道、
锁定与容量约束下按字典序优化——先最小化所有 `capacity > 0` 评审人**最终负载率**
（`分配数 / capacity`）的最大值，再最小化相对 `previous_assignments` 的改动数，
最后按 ID 破同；负载率全程以约分分数（整数分子/分母）表示与比较，**不依赖浮点数**，
响应返回最优最大负载率的约分分数与改动数。

`POST /assign-coverage` 提供**评审人下限覆盖**：稿件、评审人、主题回避、赛道限额与
总容量字段与 `/assign` 相同，评审人另可声明 `min_assignments`（本轮至少分到的稿件数，
缺省 0）。接口在满足每稿所需人数的同时满足所有评审人的下限与上限，使用
**全局带下界流**一次求出完整可行解；下限大于容量、资格不足或共享容量冲突时返回
`infeasible` 并给出结构化诊断，**不输出任何部分名单**；相同业务输入重排顺序仍得到
完全相同的名单。

`POST /assign-roles` 提供**分角色分配**：稿件按角色声明所需人数（`role_requirements`，
如 `{"chair": 1, "reviewer": 2}`），评审人声明可担任角色（`roles`）；全部稿件的全部
角色在**同一个流网络中一次全局求解**（而非按角色分别分配后拼接，避免各角色争用共享
容量），同一评审人对同一稿件**最多承担一个角色**，所有角色共享评审人总容量与赛道限额。
可行时返回按稿件、角色分组且顺序确定的完整名单；不可行时按角色返回缺口，
不输出任何部分分配。

`POST /conflict-audit` 提供**资格审计**（只读，不改动任何分配结果）：逐稿件、逐评审人
判定是否具备评审资格。沿用自稿冲突、显式回避（`conflicted_authors` /
`conflicted_manuscripts`）与主题匹配规则；另接受人员 ID 组成的**无向合作关系边**
`collaboration_edges` 与最大链长 `max_hops`（1～6），评审人到稿件**任一作者**存在
不超过 `max_hops` 跳的合作链时判为合作链利益冲突，并返回**最短人员路径**；等长路径
按完整 ID 序列字典序选取。响应按 ID 排序，分别列出每个稿件的合格评审人与不合格配对
的全部原因；主题不匹配不属于利益冲突，以 `topic_mismatch` 单独标记。

`POST /review-clock` 提供**评审工时钟重放**：对 `/assign` 名单形成的稿件—评审人配对，
携带每对正整数工作小时预算与按 UTC 时间记录的状态事件（`accept` 接受、`pause` 暂停、
`resume` 恢复、`submit` 提交）以及节假日日期，服务按事件时间重放每对的状态机，
**只在周一至周五 09:00—17:00 UTC 且非节假日**累计工作时长；暂停期间停止计时，
跨下班、周末与节假日自动顺延。响应给出各对当前状态、已用/剩余工作时长、预计截止
UTC 时刻与是否逾期（暂停中截止时刻为空，提交后按提交时刻判定逾期），结果按稿件与
评审人 ID 排序。重复配对、未知配对、同对同刻事件与非法状态转换一律返回 `422` 与
结构化问题清单，不输出任何钟结果。

`POST /decision-ranking` 提供**稿件共识排序**：接收 `/assign` 的可行分配名单与评审人
对其**已评稿件**的严格排序票，按 **Schulze 最强路径法**生成稿件共识顺序。每位评审人
最多一票；票内稿件不得重复且必须属于其分配名单；允许只排部分稿件，**未同时出现在
同一张票中的两稿不计入成对支持**。直接支持为将甲排在乙前的票数，**仅成对胜者
（`d[甲][乙] > d[乙][甲]`）的直接支持可作为路径边**，路径强度取沿途最小支持的最大值；
甲对乙的路径强度较大时甲排在前，无法区分时按稿件 ID 破同。响应返回覆盖名单中全部
稿件的确定完整顺序与全部有序稿件对的成对支持数；输入列表重排不改变结果，非法投票
（一人多票 / 票内重复 / 稿件不属于其分配名单）返回 `422` 与结构化问题清单，不输出
任何排序结果。

## 快速启动（单条命令）

```bash
docker compose up --build
```

依赖安装与初始化全部在镜像构建与容器启动时完成，无需任何额外步骤；启动后浏览器或
curl 直接访问 <http://localhost:8000> 即可（根路径返回服务信息）。

启动后访问：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000/ | 服务信息（接口与文档入口） |
| http://localhost:8000/health | 健康检查 |
| http://localhost:8000/assign | 分配接口（POST） |
| http://localhost:8000/assign-coverage | 评审人下限覆盖分配接口（POST） |
| http://localhost:8000/assign-roles | 分角色分配接口（POST） |
| http://localhost:8000/conflict-audit | 资格审计接口（POST，只读） |
| http://localhost:8000/review-clock | 评审工时钟重放接口（POST） |
| http://localhost:8000/decision-ranking | 稿件共识排序接口（POST，Schulze 最强路径） |
| http://localhost:8000/docs | Swagger 交互式文档 |
| http://localhost:8000/redoc | ReDoc 文档 |

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 容器内服务监听端口 |

宿主机端口映射在 `docker-compose.yml` 的 `ports` 中调整（默认 `8000:8000`）。

不使用 Compose 时也可直接构建运行：

```bash
docker build -t review-assign .
docker run -p 8000:8000 review-assign
```

## API 说明

### `GET /health`

```json
{"status": "ok"}
```

### `POST /assign`

请求体：

```json
{
  "required_reviewers": 2,
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"]},
    {"id": "m2", "authors": ["bob"], "topics": ["ml", "db"], "required_reviewers": 1}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 2},
    {"id": "r2", "topics": ["ml", "db"], "capacity": 1, "conflicted_authors": ["alice"]},
    {"id": "r3", "topics": ["db"], "capacity": 2, "conflicted_manuscripts": ["m1"]},
    {"id": "r4", "topics": ["ml"], "capacity": 1}
  ]
}
```

字段规则：

- `required_reviewers`（请求级，默认 `1`）：每稿默认所需评审数；稿件级
  `required_reviewers` 可对其覆盖。
- 稿件 `topics` 非空时，评审人须与其有交集才具备能力；稿件 `topics` 为空则不做主题限制。
- 冲突回避（满足任一即不可评审）：
  - 评审人 `id` 出现在稿件 `authors` 中（自稿冲突）；
  - 稿件作者与评审人 `conflicted_authors` 有交集；
  - 稿件 `id` 出现在评审人 `conflicted_manuscripts` 中。
- 分赛道容量（均可选，省略时行为与历史版本完全一致）：
  - 稿件 `track`：稿件所属赛道；
  - 评审人 `track_capacities`：`赛道 -> 非负整数限额` 的映射。稿件赛道出现在某
    评审人的映射中时，该评审人在此赛道上的分配数不得超过对应限额，且与总容量
    `capacity` 同时生效；未列入映射的赛道与不携带 `track` 的稿件只受总容量约束。
- 稿件 / 评审人 `id` 必须各自唯一，否则返回 `422`。
- `balance_workload`（可选，默认 `false`）：启用负载均衡优化，见下文
  「负载均衡模式」。

可行时响应（`200`）：

```json
{
  "status": "feasible",
  "total_required": 3,
  "total_assigned": 3,
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1", "r4"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r1"]}
  ],
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": null,
  "bottleneck_cut": null,
  "balance": null
}
```

不可行时响应（`200`，`assignments` 为 `null`，不输出部分结果）：

```json
{
  "status": "infeasible",
  "total_required": 4,
  "total_assigned": 2,
  "assignments": null,
  "unsatisfied": [
    {
      "manuscript_id": "m2",
      "required": 2,
      "assigned": 0,
      "missing": 2,
      "eligible_reviewers": 2,
      "eligible_remaining_capacity": 0,
      "candidate_gap": 0,
      "capacity_gap": 2
    }
  ],
  "shared_pool": {
    "manuscript_ids": ["m2"],
    "reviewer_ids": ["r1", "r2"],
    "missing_total": 2,
    "pool_remaining_capacity": 0,
    "pool_total_capacity": 2
  },
  "locked_conflicts": null,
  "bottleneck_cut": {
    "source_manuscripts": ["m1", "m2"],
    "source_reviewer_tracks": [],
    "source_reviewers": ["r1", "r2"],
    "demand": [],
    "eligibility": [],
    "track_capacity": [],
    "reviewer_capacity": [
      {"type": "reviewer_capacity", "capacity": 1, "manuscript_id": null, "reviewer_id": "r1", "track": null},
      {"type": "reviewer_capacity", "capacity": 1, "manuscript_id": null, "reviewer_id": "r2", "track": null}
    ],
    "max_assignable": 2,
    "shortfall": 2
  },
  "balance": null
}
```

诊断字段含义：

- `eligible_reviewers`：通过主题与冲突过滤的合格候选人数；
- `eligible_remaining_capacity`：最大流结束后这些候选对该稿赛道的剩余可接量之和
  （候选受赛道限额约束时，取总剩余容量与赛道剩余限额的较小者）；
- `candidate_gap`：候选人数缺口（`required - eligible_reviewers`，候选本身不足）；
- `capacity_gap`：容量缺口（`missing - eligible_remaining_capacity`，候选够但容量不足）；
- `shared_pool`：所有未满足稿件共享的候选池及其总/剩余容量，用于定位整体瓶颈。

#### `bottleneck_cut`：不可行的全局最小割证明

最大流结束后，从源点沿**残量容量大于 0** 的边可达的节点构成源侧集合 `S`；由
最大流最小割定理，所有 `S → 非 S` 的正向边恰好全部饱和，其容量之和（割容量）
等于最大流——即在主题、冲突、赛道限额与总容量全部约束下**至多可满足的名额数**
`max_assignable`，`shortfall = total_required - max_assignable` 为严格缺口下界。

- `source_manuscripts` / `source_reviewers`：源侧的稿件 / 评审人业务 ID（按 ID 排序）；
- `source_reviewer_tracks`：源侧的「(评审人, 赛道)」限额节点，二元组
  `[reviewer_id, track]`，按业务 ID 排序；
- 跨割饱和边按业务含义分四组列出（组内按业务 ID 确定排序，列表乱序输出不变）：

| 分组 | 边 | 携带字段 |
| --- | --- | --- |
| `demand` | 源 → 稿件（该稿需求整体跨割） | `manuscript_id`, `capacity` |
| `eligibility` | 稿件 → 评审人 / (评审人,赛道) 节点（合格关系构成的 Hall 型瓶颈） | `manuscript_id`, `reviewer_id`, `track`（走赛道节点时）, `capacity` |
| `track_capacity` | (评审人,赛道) → 评审人（赛道限额饱和，含限额为 0 的边） | `reviewer_id`, `track`, `capacity` |
| `reviewer_capacity` | 评审人 → 汇（评审总容量饱和） | `reviewer_id`, `capacity` |

示例：

- 总容量不足时，割边全在 `reviewer_capacity`（如上方示例，割容量 2 = 至多 2 个名额）；
- 合格候选不足时，割边出现在 `eligibility`：下例割容量为 1（唯一合格关系 `m1→r1`），
  证明 2 个名额至多满足 1 个；

```json
{
  "bottleneck_cut": {
    "source_manuscripts": ["m1"],
    "source_reviewer_tracks": [],
    "source_reviewers": [],
    "demand": [],
    "eligibility": [
      {"type": "eligibility", "capacity": 1, "manuscript_id": "m1", "reviewer_id": "r1", "track": null}
    ],
    "track_capacity": [],
    "reviewer_capacity": [],
    "max_assignable": 1,
    "shortfall": 1
  }
}
```

- 赛道限额为 0 时，赛道节点在源侧、评审人在汇侧，`track_capacity` 中列出容量 0 的饱和边。

**增量重排模式**下，合法锁定关系先固化（扣减稿件剩余需求、评审剩余总容量与剩余
赛道限额），瓶颈割建在该「扣除锁定占用后的残量网络」上：各边 `capacity` 为扣除
锁定后的**剩余**容量（因此可能为 0），割容量等于剩余网络最大流；`max_assignable`
为**合法锁定名额 + 割容量**（即含锁定关系在内全局至多可满足的名额数）。锁定关系
校验失败时不进行求解，`bottleneck_cut` 为 `null`（只返回 `locked_conflicts`）。

### 增量重排模式（可选）

请求携带 `previous_assignments` 和/或 `locked_assignments` 即进入增量重排模式
（两者都不携带时行为与上述普通模式完全一致）：

```json
{
  "required_reviewers": 1,
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"], "required_reviewers": 2},
    {"id": "m2", "authors": ["bob"], "topics": ["db"]}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 2},
    {"id": "r2", "topics": ["ml", "db"], "capacity": 1},
    {"id": "r3", "topics": ["db"], "capacity": 1}
  ],
  "previous_assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r2"]}
  ],
  "locked_assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1"]}
  ]
}
```

字段规则：

- `previous_assignments`：上一轮分配结果。求解在保证全局可行的前提下尽量复用这些
  关系，使**新增 + 移除的关系总数最少**；引用已不存在的稿件/评审人、或当前已不满足
  资格/约束的关系自动视为无法复用（计为移除），不影响求解。历史关系仅在赛道限额与
  总容量两级容量均允许时才会被复用。
- `locked_assignments`：必须原样保留的锁定关系。每条锁定关系必须同时满足：
  - 稿件需求：同一稿件的锁定人数不超过其 `required_reviewers`；
  - 评审资格：与普通分配完全相同的主题匹配与冲突回避校验；
  - 容量：同一评审人的锁定篇数不超过其 `capacity`，且同一赛道上的锁定篇数
    不超过其 `track_capacities` 中对应赛道的限额。

  任一锁定关系违反上述约束，即整体判定不可行（`200` + `status: "infeasible"`），
  返回结构化的 `locked_conflicts` 且**不输出任何部分分配**。
- 两个字段均为「稿件 -> 评审人列表」的条目数组；同一稿件可拆多条，重复关系自动去重。

上述例子的可行响应（`m1->r1` 被锁定保留，`m1->r2` 复用上一轮；`r2` 容量被 `m1` 占满，
`m2` 被迫由 `r2` 调整为 `r3`，共 1 新增 1 移除，已是最少改动）：

```json
{
  "status": "feasible",
  "total_required": 3,
  "total_assigned": 3,
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r3"]}
  ],
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": null,
  "bottleneck_cut": null,
  "balance": null
}
```

锁定冲突时的响应（`200`，`assignments` 为 `null`，不输出部分结果）：

```json
{
  "status": "infeasible",
  "total_required": 3,
  "total_assigned": 0,
  "assignments": null,
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": [
    {
      "type": "reviewer_capacity_exceeded",
      "manuscript_id": null,
      "reviewer_id": "r1",
      "locked": 3,
      "limit": 2,
      "track": null,
      "detail": "评审人 r1 被锁定 3 篇稿件，超出其容量 2"
    }
  ],
  "bottleneck_cut": null,
  "balance": null
}
```

`locked_conflicts[].type` 取值：

| type | 含义 |
| --- | --- |
| `unknown_manuscript` / `unknown_reviewer` | 锁定关系引用了不存在的稿件 / 评审人 |
| `self_conflict` | 评审人是稿件作者（自稿冲突） |
| `author_conflict` | 评审人与稿件作者存在利益冲突 |
| `manuscript_conflict` | 评审人已声明回避该稿件 |
| `topic_mismatch` | 评审人主题能力与稿件主题不匹配 |
| `reviewer_capacity_exceeded` | 评审人锁定篇数超出总容量（`locked` / `limit` 给出数值） |
| `reviewer_track_capacity_exceeded` | 评审人在某赛道的锁定篇数超出赛道限额（`track` / `locked` / `limit` 给出赛道与数值） |
| `manuscript_requirement_exceeded` | 稿件锁定人数超出所需评审数（`locked` / `limit` 给出数值） |

增量模式下若锁定关系全部合法但整体仍不可行（容量/候选不足），响应与普通模式
不可行情形相同：`unsatisfied` + `shared_pool` 缺口诊断，`locked_conflicts` 为
`null`，并同样给出 `bottleneck_cut` 最小割证明（割边容量为扣除合法锁定占用后的
剩余容量，`max_assignable` 含已固化的锁定名额）。

### 分赛道容量（可选）

稿件可声明所属赛道 `track`，评审人可通过 `track_capacities` 声明分赛道限额。
映射中出现的赛道按对应非负整数限额，未列出的赛道只受总容量约束；赛道限额与
总容量在**同一个流网络中联立求解**（普通最大流与增量最小费用流一致），而非
分赛道独立求解后拼接：

```json
{
  "required_reviewers": 1,
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"], "track": "poster"},
    {"id": "m2", "authors": ["bob"], "topics": ["ml"], "track": "poster"},
    {"id": "m3", "authors": ["carol"], "topics": ["ml"], "track": "oral"}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 3, "track_capacities": {"poster": 1}},
    {"id": "r2", "topics": ["ml"], "capacity": 1}
  ]
}
```

`r1` 总容量 3 但 poster 赛道限额 1：两篇 poster 至多一篇分给 `r1`，另一篇只能给
`r2`；oral 赛道未列入 `r1` 的映射，只受其总容量约束。可行响应（`200`）：

```json
{
  "status": "feasible",
  "total_required": 3,
  "total_assigned": 3,
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r2"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r1"]},
    {"manuscript_id": "m3", "reviewer_ids": ["r1"]}
  ],
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": null,
  "bottleneck_cut": null,
  "balance": null
}
```

增量重排模式下，锁定关系同样占用赛道限额：若 `locked_assignments` 使某评审人
在某赛道的锁定篇数超出对应限额，整体返回 `infeasible` 与结构化
`locked_conflicts`（类型 `reviewer_track_capacity_exceeded`），不输出部分结果。
对上述请求追加锁定后：

```json
{
  "locked_assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r1"]}
  ]
}
```

响应（`200`，`assignments` 为 `null`）：

```json
{
  "status": "infeasible",
  "total_required": 3,
  "total_assigned": 0,
  "assignments": null,
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": [
    {
      "type": "reviewer_track_capacity_exceeded",
      "manuscript_id": null,
      "reviewer_id": "r1",
      "locked": 2,
      "limit": 1,
      "track": "poster",
      "detail": "评审人 r1 在赛道 poster 被锁定 2 篇稿件，超出其赛道限额 1"
    }
  ],
  "bottleneck_cut": null,
  "balance": null
}
```

### 负载均衡模式（可选）

请求携带 `"balance_workload": true` 时启用负载均衡优化：完整可行解在资格、
赛道、锁定与容量约束下按**字典序**依次优化——

1. **最小化最大负载率**：所有 `capacity > 0` 的评审人中，最终负载率
   `分配数 / capacity` 的最大值最小（锁定关系计入最终负载）；
2. **最少改动**：在最大负载率已达最优的解中，相对 `previous_assignments` 的
   新增 + 移除关系总数最小（`previous_assignments` 缺省时该级无区分作用，
   改动数恒等于分配总数）；
3. **ID 破同**：仍并列时按 ID 顺序确定性选取（与其他模式相同）。

负载率的表示与比较全程使用**约分分数（整数分子/分母）**，不依赖浮点数：
最优值必为某评审人 `最终负载 / capacity` 的分数（约分后分母不超过最大容量
`max_cap`），求解器先在 `1/max_cap` 整数网格上二分，把最优值定位到宽度
`1/max_cap` 的区间，再在该区间内至多 `max_cap` 个精确分数候选上二分出最小
可行负载率（每次判定为一次最大流可行性检查，评审人容量按
`floor(负载率 × capacity)` 收紧）；随后把收紧后的容量交给最小费用最大流
完成第 2、3 级优化。

可行时响应新增 `balance` 字段（未启用或不可行时为 `null`）：

```json
{
  "status": "feasible",
  "total_required": 2,
  "total_assigned": 2,
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r2"]}
  ],
  "unsatisfied": null,
  "shared_pool": null,
  "locked_conflicts": null,
  "bottleneck_cut": null,
  "balance": {
    "max_load_ratio": {"numerator": 1, "denominator": 2},
    "changes": 2
  }
}
```

- `balance.max_load_ratio`：最优最大负载率的**约分分数**（`numerator` 与
  `denominator` 互质、分母为正；负载率为 0 时表示为 `0/1`）；
- `balance.changes`：相对 `previous_assignments` 的新增 + 移除关系总数
  （引用已不存在稿件/评审人的历史关系按移除计，与增量模式语义一致）。

`balance_workload` 可与 `previous_assignments` / `locked_assignments` / 分赛道
容量任意组合：锁定关系照常强制保留并计入最终负载；无完整可行解时响应与对应
模式的不可行情形完全一致（`unsatisfied` / `shared_pool` / `bottleneck_cut`
诊断，`balance` 为 `null`），不输出任何部分分配。

### `POST /assign-coverage`（评审人下限覆盖）

稿件、评审人、主题回避、赛道限额与总容量字段与 `POST /assign` 完全相同；评审人
另可声明 `min_assignments`（非负整数，缺省 `0`）：本轮**至少**分到的稿件数。
接口在满足每稿所需人数的同时满足所有评审人的下限与上限，全部约束在同一个
**带下界流**网络中联立求解（而非逐稿贪心或先分后补）；可行时返回完整名单，
不可行时不输出任何部分名单。

请求体：

```json
{
  "required_reviewers": 1,
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"], "track": "poster"},
    {"id": "m2", "authors": ["bob"], "topics": ["ml"], "track": "poster"},
    {"id": "m3", "authors": ["carol"], "topics": ["db"]}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 2, "min_assignments": 1,
     "track_capacities": {"poster": 1}},
    {"id": "r2", "topics": ["ml", "db"], "capacity": 2, "min_assignments": 1},
    {"id": "r3", "topics": ["db"], "capacity": 2, "min_assignments": 1}
  ]
}
```

字段规则：

- 评审人 `min_assignments`：本轮至少分到的稿件数（下界，缺省 `0`）；与
  `capacity`（上界）、`track_capacities`（赛道限额）同时生效。
- 其余字段（`required_reviewers` / `authors` / `topics` / `track` / `capacity` /
  `conflicted_authors` / `conflicted_manuscripts` / `track_capacities`）语义与
  `POST /assign` 完全相同；稿件 / 评审人 `id` 必须各自唯一，否则返回 `422`。

可行时响应（`200`）：完整名单（稿件按 ID 排序、名单按 ID 排序），
`total_assigned == total_required`；相同业务输入（甚至不同的列表顺序）必然产生
完全相同的名单：

```json
{
  "status": "feasible",
  "total_required": 3,
  "total_min_required": 3,
  "total_assigned": 3,
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r2"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r1"]},
    {"manuscript_id": "m3", "reviewer_ids": ["r3"]}
  ],
  "reviewer_conflicts": null,
  "unsatisfied": null,
  "shared_pool": null,
  "reviewer_shortfalls": null
}
```

（`r1` 的 poster 赛道限额 1：两篇 poster 至多一篇分给 `r1`；三名评审人各声明
下限 1，总需求 3 恰好每人 1 篇。）

不可行时响应（`200`，`assignments` 为 `null`，`total_assigned` 为 `0`，
**不输出任何部分名单**），并按原因给出诊断：

- `reviewer_conflicts`：不需求解即可判定的结构性冲突（此时不再进行流量求解，
  `unsatisfied` / `reviewer_shortfalls` 为 `null`）：

  | type | 含义 |
  | --- | --- |
  | `min_exceeds_capacity` | 下限大于评审人容量（`limit` 为容量） |
  | `min_exceeds_eligible` | 下限大于该评审人在资格与赛道限额下的个人可接上限（`limit` 为该上限） |

- 结构性校验通过但全局不可行时，以下两侧诊断**至少其一非空**：
  - `unsatisfied` / `shared_pool`：稿件侧缺口（字段含义与 `/assign` 相同）——
    即使完全不考虑下限，稿件也无法被完整覆盖（资格不足或共享容量冲突）；
  - `reviewer_shortfalls`：评审人侧下限缺口——即使不要求稿件被完整覆盖，这些
    评审人也凑不够下限（`attained` 为全局最优努力下实际能分到的数量，
    `missing` 为缺口）。

下限大于容量的响应示例：

```json
{
  "status": "infeasible",
  "total_required": 1,
  "total_min_required": 2,
  "total_assigned": 0,
  "assignments": null,
  "reviewer_conflicts": [
    {
      "type": "min_exceeds_capacity",
      "reviewer_id": "r1",
      "min_assignments": 2,
      "limit": 1,
      "detail": "评审人 r1 声明的下限 2 超出其容量 1"
    }
  ],
  "unsatisfied": null,
  "shared_pool": null,
  "reviewer_shortfalls": null
}
```

评审人侧下限缺口的响应示例（`r1`、`r2` 均声明回避 `m2`，只能争 `m1` 的
1 个名额，最优努力下只有一人能达标）：

```json
{
  "status": "infeasible",
  "total_required": 2,
  "total_min_required": 2,
  "total_assigned": 0,
  "assignments": null,
  "reviewer_conflicts": null,
  "unsatisfied": null,
  "shared_pool": null,
  "reviewer_shortfalls": [
    {"reviewer_id": "r2", "min_assignments": 1, "attained": 0, "missing": 1}
  ]
}
```

### `POST /assign-roles`（分角色分配）

稿件按**角色**声明所需评审人数，评审人声明**可担任角色**；全部稿件的全部角色在
**同一个流网络中一次全局求解**（而非按角色分别分配后拼接，避免各角色争用共享
容量）。主题匹配、利益冲突（含自稿回避）、总容量与分赛道限额规则与
`POST /assign` 完全一致，另加两条角色规则：

- 评审人只能担任其 `roles` 中声明的角色（缺省为空，即不能担任任何角色）；
- 同一评审人对同一稿件**最多承担一个角色**（容量再大也不能身兼两角）；
- 所有角色、所有稿件共享评审人的总容量与赛道限额（每承担一个角色计 1 份负载）。

请求体：

```json
{
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"],
     "role_requirements": {"chair": 1, "reviewer": 1}},
    {"id": "m2", "authors": ["bob"], "topics": ["db"], "track": "poster",
     "role_requirements": {"reviewer": 2}}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 2, "roles": ["chair", "reviewer"]},
    {"id": "r2", "topics": ["ml", "db"], "capacity": 2, "roles": ["reviewer"]},
    {"id": "r3", "topics": ["db"], "capacity": 2, "roles": ["reviewer"],
     "track_capacities": {"poster": 1}}
  ]
}
```

字段规则：

- 稿件 `role_requirements`：`角色 -> 非负整数人数` 的映射（必填，可为空对象）；
  需求为 0 的角色在可行响应中仍会出现（空名单），保证输出结构完整确定。
- 评审人 `roles`：可担任角色列表（缺省为空列表，重复项自动去重）。
- 其余字段（`authors` / `topics` / `capacity` / `conflicted_authors` /
  `conflicted_manuscripts` / `track` / `track_capacities`）语义与
  `POST /assign` 完全相同。
- 稿件 / 评审人 `id` 必须各自唯一，否则返回 `422`。

可行时响应（`200`）：按稿件、角色分组的完整名单——稿件按 ID 排序、角色按名称
排序、名单按 ID 排序，相同输入（甚至不同的列表顺序）必然产生完全相同的输出：

```json
{
  "status": "feasible",
  "total_required": 4,
  "total_assigned": 4,
  "assignments": [
    {"manuscript_id": "m1", "roles": [
      {"role": "chair", "reviewer_ids": ["r1"]},
      {"role": "reviewer", "reviewer_ids": ["r2"]}
    ]},
    {"manuscript_id": "m2", "roles": [
      {"role": "reviewer", "reviewer_ids": ["r2", "r3"]}
    ]}
  ],
  "unsatisfied": null
}
```

不可行时响应（`200`，`assignments` 为 `null`，**不输出任何部分分配**），按
（稿件, 角色）给出缺口诊断：

```json
{
  "status": "infeasible",
  "total_required": 3,
  "total_assigned": 2,
  "assignments": null,
  "unsatisfied": [
    {
      "manuscript_id": "m1",
      "role": "chair",
      "required": 1,
      "assigned": 0,
      "missing": 1,
      "eligible_reviewers": 0,
      "eligible_remaining_capacity": 0
    }
  ]
}
```

诊断字段含义：

- `eligible_reviewers`：通过主题与冲突过滤、且声明可担任该角色的候选人数；
- `eligible_remaining_capacity`：最大流结束后这些候选对该稿赛道的剩余可接量之和
  （候选受赛道限额约束时，取总剩余容量与赛道剩余限额的较小者）。

### `POST /conflict-audit`（资格审计，只读）

逐稿件、逐评审人审计评审资格，**只审计资格，不输出也不改动任何分配结果**。

资格规则与 `POST /assign` 完全一致：

- 自稿冲突（`self_conflict`）：评审人 `id` 出现在稿件 `authors` 中；
- 显式作者回避（`author_conflict`）：稿件作者与评审人 `conflicted_authors` 有交集；
- 显式稿件回避（`manuscript_conflict`）：稿件 `id` 出现在评审人
  `conflicted_manuscripts` 中；
- 主题匹配：稿件 `topics` 非空时，评审人 `topics` 须与其有交集。主题不匹配
  **不是利益冲突**，在响应中以 `topic_mismatch: true` 单独标记（可与任一利益
  冲突原因并存）。

**合作链利益冲突**（`collaboration_chain_conflict`）：在由人员 ID 组成的无向
合作图 `collaboration_edges`（`[人员A, 人员B]` 边列表）上，评审人到稿件**任一
作者**存在长度不超过 `max_hops` 跳（1～6）的路径时判为利益冲突；边无方向，
边中人员不必是稿件作者或评审人（可仅作为合作链中间节点）。命中时返回：

- `collaboration_path`：最短人员路径 `[评审人, 中间人员..., 作者]`；
- `hops`：最短路径跳数，即 `len(collaboration_path) - 1`。

等长最短路径按**完整 ID 序列字典序**取最小（先逐跳取字典序最小的可完成路径，
而非仅比较作者 ID）。评审人本人是作者时按 `self_conflict` 处理（0 跳不计入合作
链）；到**其他**合著者仍存在 1～`max_hops` 跳路径时，两个原因同时列出。

请求体：

```json
{
  "max_hops": 2,
  "manuscripts": [
    {"id": "m1", "authors": ["alice"], "topics": ["ml"]},
    {"id": "m2", "authors": ["bob"], "topics": ["db"]}
  ],
  "reviewers": [
    {"id": "r1", "topics": ["ml"], "capacity": 2},
    {"id": "r2", "topics": ["nlp"], "capacity": 1,
     "conflicted_manuscripts": ["m1"]},
    {"id": "r3", "topics": ["ml"], "capacity": 1}
  ],
  "collaboration_edges": [
    ["r3", "x"],
    ["x", "alice"]
  ]
}
```

字段规则：

- `max_hops`（必填，1～6）：合作链利益冲突的最大跳数，超出范围返回 `422`。
- `manuscripts` / `reviewers`：与 `POST /assign` 相同的稿件 / 评审人对象；
  审计只读取资格相关字段（`authors` / `topics` / `conflicted_authors` /
  `conflicted_manuscripts`），`capacity`、`track_capacities`、
  `required_reviewers` 等分配字段**不影响**审计结果（仍按原规则校验取值范围）。
- `collaboration_edges`（可选，缺省为空）：人员 ID 二元组数组。边为无向边，
  **重复边、反向边（`[a,b]` 与 `[b,a]`）与自环（`[a,a]`）自动忽略**，
  端点 ID 不能为空；边及各列表的输入顺序不影响结果。
- 稿件 / 评审人 `id` 必须各自唯一，否则返回 `422`。

响应（`200`）：`audits` 按稿件 ID 排序；每个稿件的 `eligible_reviewer_ids`
（无任何利益冲突原因且主题匹配的评审人）与 `ineligible_pairs`（其余全部评审人）
均按评审人 ID 排序，每个不合格配对列出其**全部**原因。相同输入（含重复边与列表
乱序）必然产生逐字节相同的输出：

```json
{
  "max_hops": 2,
  "audits": [
    {
      "manuscript_id": "m1",
      "eligible_reviewer_ids": ["r1"],
      "ineligible_pairs": [
        {
          "reviewer_id": "r2",
          "reasons": ["manuscript_conflict"],
          "topic_mismatch": true,
          "collaboration_path": null,
          "hops": null
        },
        {
          "reviewer_id": "r3",
          "reasons": ["collaboration_chain_conflict"],
          "topic_mismatch": false,
          "collaboration_path": ["r3", "x", "alice"],
          "hops": 2
        }
      ]
    },
    {
      "manuscript_id": "m2",
      "eligible_reviewer_ids": [],
      "ineligible_pairs": [
        {"reviewer_id": "r1", "reasons": [], "topic_mismatch": true,
         "collaboration_path": null, "hops": null},
        {"reviewer_id": "r2", "reasons": [], "topic_mismatch": true,
         "collaboration_path": null, "hops": null},
        {"reviewer_id": "r3", "reasons": [], "topic_mismatch": true,
         "collaboration_path": null, "hops": null}
      ]
    }
  ]
}
```

返回字段含义：

| 字段 | 含义 |
| --- | --- |
| `max_hops` | 本次审计使用的最大合作链跳数（与请求一致） |
| `audits[].manuscript_id` | 稿件 ID（按 ID 排序） |
| `audits[].eligible_reviewer_ids` | 合格评审人 ID：无任何利益冲突原因且主题匹配（按 ID 排序） |
| `ineligible_pairs[].reviewer_id` | 不合格配对的评审人 ID（按 ID 排序） |
| `ineligible_pairs[].reasons` | 利益冲突原因列表，可多个并存，固定顺序为 `self_conflict` → `author_conflict` → `manuscript_conflict` → `collaboration_chain_conflict`；仅主题不匹配时为空列表 |
| `ineligible_pairs[].topic_mismatch` | 主题不匹配单独标记（`true` 时不影响 `reasons`） |
| `ineligible_pairs[].collaboration_path` | 合作链冲突时的最短人员路径 `[评审人, ..., 作者]`；无合作链冲突时为 `null` |
| `ineligible_pairs[].hops` | 最短合作链跳数（`len(collaboration_path) - 1`）；无合作链冲突时为 `null` |

### `POST /review-clock`（评审工时钟重放）

对 `/assign` 名单形成的**稿件—评审人配对**，携带每对正整数**工作小时预算**与按
UTC 时间记录的状态事件，服务按事件时间逐对重放状态机，并按统一工作日历累计工时。
接口本身不做分配计算（名单由调用方从 `/assign` 响应取得），服务无状态。

**工作日历（UTC）**：周一至周五 `09:00—17:00` 为工作时段（每个工作日 8 小时），
请求给出的 `holidays` 节假日整日不计工时；计时只累计落在工作时段内的时间，
跨下班、周末与节假日自动顺延、不消耗预算。

**状态机**：

```
pending ──accept──▶ active ──pause──▶ paused ──resume──▶ active
                       │                  │
                       └────submit───────▶ submitted（终态；paused 不能直接提交）
```

- `pending`（待接受）：尚无事件；工时为 0、无截止时刻。
- `active`（计时中）：`accept` / `resume` 后开始累计工时。
- `paused`（已暂停）：`pause` 后停止计时，工时冻结，**截止时刻为 `null`**；
  `resume` 后以恢复时刻与当时剩余预算重新投影截止时刻。
- `submitted`（已提交）：终态；工时与截止判定**冻结在提交时刻**——
  提交时刻晚于截止时刻（等价于累计工时超过预算）即 `overdue: true`，
  与查询时刻早晚无关。

请求体：

```json
{
  "pairs": [
    {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 10},
    {"manuscript_id": "m1", "reviewer_id": "r2", "budget_hours": 8}
  ],
  "events": [
    {"manuscript_id": "m1", "reviewer_id": "r1", "type": "accept",
     "at": "2026-09-21T10:00:00Z"},
    {"manuscript_id": "m1", "reviewer_id": "r1", "type": "pause",
     "at": "2026-09-21T12:00:00Z"},
    {"manuscript_id": "m1", "reviewer_id": "r1", "type": "resume",
     "at": "2026-09-22T10:00:00Z"},
    {"manuscript_id": "m1", "reviewer_id": "r2", "type": "accept",
     "at": "2026-09-21T08:00:00Z"}
  ],
  "holidays": ["2026-09-23"],
  "query_at": "2026-09-22T11:00:00Z"
}
```

字段规则：

- `pairs`（必填，至少 1 对）：由 `/assign` 名单形成的配对；同一
  `(manuscript_id, reviewer_id)` 重复出现返回 `422`（`duplicate_pair`）。
  `budget_hours` 为正整数工作小时预算（1～100000）。
- `events`（可选，缺省为空）：元素为 `{manuscript_id, reviewer_id, type, at}`；
  `type` 取值 `accept` / `pause` / `resume` / `submit`，`at` 为 UTC 时刻
  （建议以 `Z` 结尾的 ISO 8601；不带时区偏移的日期时间按 UTC 解释）。事件
  **无需按时间排序**，服务按 `at` 重放，输入顺序不影响结果。
- `holidays`（可选，缺省为空）：`YYYY-MM-DD` 日期列表，按 UTC 日期整日排除，
  重复日期自动去重。
- `query_at`（必填）：查询时刻（UTC）。仅重放 `at <= query_at` 的事件；
  之后的事件整段忽略（既不改变状态也不参与校验），钟面为该时刻的快照。
- 校验问题（均返回 `422`，见下表）可一次返回多条，按类型与 ID 确定性排序，
  此时不输出任何钟结果：

| 问题类型 | 含义 |
| --- | --- |
| `duplicate_pair` | `pairs` 中同一配对重复出现 |
| `unknown_pair` | 事件引用了未在 `pairs` 中声明的配对 |
| `duplicate_event` | 同一配对在同一 UTC 时刻有两条及以上事件（与事件类型是否相同无关） |
| `invalid_transition` | 事件在当前状态下非法（如未接受先暂停、暂停中直接提交、提交后再来事件） |

非法事件在重放中跳过（其后的合法事件照常重放），所有问题收集后一并返回。

可行时响应（`200`）：`clocks` 按 `(manuscript_id, reviewer_id)` 排序。工时以小时
给出：整小时为整数（如 `2`），不足整小时为至多 6 位小数的数（5 秒精度，如
`0.001389`）；`remaining_hours = budget_hours - worked_hours`，逾期未提交时
可能为负。`deadline` 为 ISO 8601 UTC 时刻（`Z` 结尾）。

```json
{
  "query_at": "2026-09-22T11:00:00Z",
  "holidays": ["2026-09-23"],
  "clocks": [
    {
      "manuscript_id": "m1",
      "reviewer_id": "r1",
      "status": "active",
      "worked_hours": 3,
      "remaining_hours": 7,
      "deadline": "2026-09-24T10:00:00Z",
      "overdue": false
    },
    {
      "manuscript_id": "m1",
      "reviewer_id": "r2",
      "status": "active",
      "worked_hours": 10,
      "remaining_hours": -2,
      "deadline": "2026-09-21T17:00:00Z",
      "overdue": true
    }
  ]
}
```

上例说明：

- `m1/r1`：周一 10:00—12:00 工作 2h 后暂停（暂停不计工时），周二 10:00 恢复，
  至查询时刻 11:00 共工作 3h；恢复时剩余 8h，预算投影跨周二（7h）后到周四
  （周三为节假日）10:00，故 `deadline = 2026-09-24T10:00:00Z`。
- `m1/r2`：周一 08:00（上班前）接受，计时从 09:00 起，当天 8h 用满预算，
  截止为周一 17:00；查询时已工作 10h（周一 8h + 周二 2h），`overdue: true`，
  剩余为 -2h。

被拒绝时响应（`422`，与请求体字段错误的 FastAPI 校验响应同为 422，但本结构为
`errors` 数组）：

```json
{
  "errors": [
    {
      "type": "invalid_transition",
      "manuscript_id": "m1",
      "reviewer_id": "r1",
      "event_type": "pause",
      "at": "2026-09-21T10:00:00Z",
      "detail": "配对 m1 -> r1 在 2026-09-21T10:00:00+00:00 的 pause 事件非法：当前状态为 pending，仅允许：accept"
    }
  ]
}
```

逾期判定细则：

| 状态 | 截止时刻 | 逾期判定 |
| --- | --- | --- |
| `pending` | `null` | `false` |
| `active` | 进入当前计时段（accept/resume）时按当时剩余预算投影 | `query_at > deadline` |
| `paused` | `null`（无法预计恢复时间） | `false` |
| `submitted` | 最后一个计时段的投影（冻结） | 提交时刻 `> deadline`，即提交时累计工时超过预算 |

截止时刻恰等于查询/提交时刻时**不算逾期**。接受事件落在非工作时刻（上班前、
下班后、周末或节假日）时，计时段起点钳制到其后第一个工作瞬间（通常为当天/次一
工作日 09:00）。

### `POST /decision-ranking`（稿件共识排序）

接收 `/assign` 的**可行分配名单**与评审人对其**已评稿件**的严格排序票，按
**Schulze 最强路径法（Schulze method）**在全部稿件上生成确定性的共识顺序。
接口本身不做分配计算（名单由调用方从 `/assign` 响应取得），服务无状态。

投票规则：

- **每位评审人最多一票**：同一 `reviewer_id` 出现两张票即非法
  （`duplicate_ballot`）。
- **票内稿件不得重复**（`duplicate_manuscript`），且每一篇都**必须属于该评审人的
  分配名单**（`manuscript_not_assigned`）；评审人只能对自己被分到的稿件投票。
- **允许只排部分稿件**：`ranking` 是严格的线性顺序（靠前 = 更偏好），未排出的稿件
  不参与该票的任何成对比较；空列表 `[]` 等价于不投票。
- **两稿仅在同一张票中同时出现时才计入成对支持**：某票未同时排出甲、乙，则该票
  既不支持甲在前，也不支持乙在前（区别于「未排 = 垫底」的计法）。

Schulze 最强路径（严格按以下规则）：

1. **直接成对支持** `d[甲][乙]` = 将甲排在乙之前的票数（两稿须同时出现在该票中）。
2. **路径边**：仅当 `d[甲][乙] > d[乙][甲]`（甲是该成对比较的胜者）时，
   `甲 → 乙` 才作为一条边，边权为 `d[甲][乙]`；成对平局或负方的直接支持
   **不得作为路径边**。
3. **路径强度**：一条路径的强度 = 沿途各边边权的**最小值**；甲对乙的路径强度
   `p[甲][乙]` 是甲到乙**全部路径强度的最大值**（最宽路径，用 Floyd-Warshall 计算）。
4. **共识序**：以 `p[甲][乙] > p[乙][甲]`（甲严格强于乙）为有向边构成**无环有向图**
   （DAG），在其上取**按稿件 ID 破同的拓扑序**——每一步都在当前零入度（已无更强者
   在前）的稿件中选**稿件 ID 最小**者输出（min-heap Kahn）。因此：
   - 任一严格强于关系 `p[甲][乙] > p[乙][甲]` 都保证甲排在乙前（拓扑序的线性扩张）；
   - 无严格关系（路径强度无法区分）的稿件之间，由「谁先成为零入度候选、同批取最小
     ID」确定性破同；在两人全程势均力敌的常见情形下即退化为稿件 ID 升序。

   注意部分票会让「无差异」关系本身**不传递**（可能 `p[甲][丙] == p[丙][甲]`、
   `p[丙][乙] == p[乙][丙]` 但 `p[甲][乙] > p[乙][甲]`），故不能把「逐对比较路径
   强度、相等再比 ID」直接当作通用排序的比较器（会形成比较器环而漏排）；按 ID
   破同的拓扑序是满足全部严格强于关系的确定线性扩张，全票（每票排出全部稿件）时
   与通常 Schulze 排序结果一致。

请求体：

```json
{
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2", "r3"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r1", "r2", "r3"]},
    {"manuscript_id": "m3", "reviewer_ids": ["r1", "r2", "r3"]}
  ],
  "ballots": [
    {"reviewer_id": "r1", "ranking": ["m1", "m2", "m3"]},
    {"reviewer_id": "r2", "ranking": ["m2", "m3", "m1"]},
    {"reviewer_id": "r3", "ranking": ["m3", "m1", "m2"]}
  ]
}
```

字段规则：

- `assignments`（必填，可为空列表）：`/assign` 可行响应的 `assignments` 字段
  （`稿件 -> 评审人 ID 列表`）；条目与评审人重复自动去重，`reviewer_ids` 可为空
  列表（该稿本轮无人可评，但仍参与最终排序）。
- `ballots`（可选，缺省为空）：元素为 `{reviewer_id, ranking}`；`ranking` 为从最前
  到最后的稿件 ID 列表，允许只排部分稿件，空列表等价于不投票。
- 任何模式级校验问题（见下表）均返回 `422`，问题一次性收集、按
  `(type, reviewer_id, manuscript_id)` 确定性排序，**不输出任何排序结果**：

| 问题类型 | 含义 |
| --- | --- |
| `duplicate_ballot` | 同一评审人提交了多张票（每人最多一票） |
| `duplicate_manuscript` | 票内某稿件 ID 重复出现（严格票不得重复） |
| `manuscript_not_assigned` | 票内稿件不在该评审人的可行分配名单中（含评审人不在任何名单中） |

可行时响应（`200`）：`ranking` 为覆盖 `assignments` 中**全部稿件**（每篇恰好一次）
的共识完整顺序；`pair_support` 列出全部**有序稿件对**（`a != b`，不含对角）的直接
成对支持数 `support`，按 `(manuscript_id_a, manuscript_id_b)` 排序。相同业务输入
（含名单、票、稿件顺序任意重排与重复去重）必然产生逐字节相同的输出：

```json
{
  "ranking": ["m1", "m2", "m3"],
  "pair_support": [
    {"manuscript_id_a": "m1", "manuscript_id_b": "m2", "support": 2},
    {"manuscript_id_a": "m1", "manuscript_id_b": "m3", "support": 1},
    {"manuscript_id_a": "m2", "manuscript_id_b": "m1", "support": 1},
    {"manuscript_id_a": "m2", "manuscript_id_b": "m3", "support": 2},
    {"manuscript_id_a": "m3", "manuscript_id_b": "m1", "support": 2},
    {"manuscript_id_a": "m3", "manuscript_id_b": "m2", "support": 1}
  ]
}
```

上例为 3 张全序票的循环：`d` 矩阵为每张票贡献一条严格序，三方成对支持均为
`2:1`、胜者边成环（`m1→m2`、`m2→m3`、`m3→m1`，边权皆 2）；任意一方的最强路径
强度也皆为 2（沿环上的两步路径 `min(2,2)=2`），路径无法区分，按稿件 ID 升序得
`m1, m2, m3`。最强路径在更一般的例子中会打破「仅看直接多数」的循环：若甲直接小负
于乙、但甲经丙到乙的最宽路径强于乙到甲的最强路径，则甲仍排在乙前。

无票（`ballots` 为空或仅含空票）时全部路径强度为 0，共识顺序完全由稿件 ID 升序给出；
只排一篇的票不产生任何成对支持，该稿与未被同票比较的稿件之间同样由 ID 破同。

被拒绝时响应（`422`，与请求体字段错误的 FastAPI 校验响应同为 422，但本结构为
`errors` 数组）：

```json
{
  "errors": [
    {
      "type": "manuscript_not_assigned",
      "reviewer_id": "r9",
      "manuscript_id": "m2",
      "detail": "稿件 m2 不在评审人 r9 的可行分配名单中，评审人只能对分配给自己的稿件投票"
    }
  ]
}
```

### `POST /schedule-discussions`（讨论时段排期）

评审会前把 `/assign` **可行分配结果中的稿件排入讨论时段**。接收：

- `assignments`：至多 **8 篇**稿件及其评审人名单（取自 `/assign` 的可行结果；
  稿件 ID 不得重复，名单内重复评审人 ID 自动去重，`reviewer_ids` 可为空列表）；
- `slots`：至多 **8 个**候选讨论时段，每个时段带非负整数 `capacity`
  （该时段最多讨论几篇稿件，`0` 表示该时段不可排稿）；时段 ID 不得重复；
- `reviewer_availability`：每位评审人可出席的时段列表；同一评审人至多一条
  记录，列表内重复时段自动去重，允许空列表（该评审人无任何可出席时段）。

在以下全局约束下求**完整**排期（每篇稿件恰好排入一个时段）：

1. 该稿件的**全体评审人在该时段都可出席**（取各人可出席时段的交集）；
2. **同一评审人在同一时段最多讨论一篇稿件**（即使时段容量未满，两篇稿件共享的
   评审人也不能在同一时段同时参加两场讨论）；
3. 该时段被排入的稿件数不超过其 `capacity`。

存在多个可行方案时，按**稿件 ID 升序**排列稿件，逐位比较对应时段 ID，返回
**字典序最小**的方案（时段按 ID 字符串比较，与输入列表顺序无关）。输入中稿件、
时段、评审人、时段引用等列表任意重排（以及名单内重复去重）都产生逐字节相同的
输出。**不存在完整方案时返回 `infeasible` 且 `schedule` 为 `null`，不输出
任何部分排期。**

请求体：

```json
{
  "assignments": [
    {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2"]},
    {"manuscript_id": "m2", "reviewer_ids": ["r2", "r3"]}
  ],
  "slots": [
    {"id": "s1", "capacity": 1},
    {"id": "s2", "capacity": 1}
  ],
  "reviewer_availability": [
    {"reviewer_id": "r1", "slot_ids": ["s1"]},
    {"reviewer_id": "r2", "slot_ids": ["s1", "s2"]},
    {"reviewer_id": "r3", "slot_ids": ["s2"]}
  ]
}
```

可行时响应（`200`，`schedule` 按稿件 ID 排序）：

```json
{
  "status": "feasible",
  "schedule": [
    {"manuscript_id": "m1", "slot_id": "s1"},
    {"manuscript_id": "m2", "slot_id": "s2"}
  ]
}
```

上例中 m1 的两位评审人共同只能出席 s1、m2 中 r3 只能出席 s2，故唯一完整
排期即 m1→s1、m2→s2。字典序优化意味着即使两篇稿件都能放进 ID 更小的时段，
也会在全局约束允许的前提下优先让稿件 ID 最小的稿件占用最小时段——若该选择会使
后续稿件无时段可排（争用共享评审人或容量不足），求解器回溯后取次优选择。

无完整方案时（`200`，业务状态为 `infeasible`，不输出部分排期）：

```json
{"status": "infeasible", "schedule": null}
```

边界约定：

- `assignments` 为空列表时，完整排期就是空方案：
  `{"status": "feasible", "schedule": []}`；
- 稿件没有评审人（`reviewer_ids: []`）时不受出席交集约束，只受时段容量限制；
- 评审人显式声明空可出席列表（`slot_ids: []`）时，含该评审人的稿件无法
  排入任何时段；
- 声明了可出席时段但不参与任何稿件的评审人是多余信息，不构成错误；
- `slots` 为空列表而有待排稿件时判定 `infeasible`（输入本身合法）。

非法输入整体拒绝（`422`，与请求体字段错误的 FastAPI 校验响应同为 422，但本
结构为 `errors` 数组），问题一次性收集、按 `(type, manuscript_id, slot_id,
reviewer_id)` 确定性排序，**不输出任何排期**：

| 问题类型 | 含义 |
| --- | --- |
| `too_many_manuscripts` | 稿件数超过 8 篇上限 |
| `too_many_slots` | 候选时段数超过 8 个上限 |
| `duplicate_manuscript` | 分配名单中出现重复稿件 ID |
| `duplicate_slot` | 候选时段中出现重复时段 ID |
| `duplicate_availability` | 同一评审人的可出席时段声明出现多条 |
| `reviewer_availability_undeclared` | 分配名单中的评审人没有可出席时段声明（含未列出该评审人） |
| `unknown_slot_reference` | 评审人可出席列表引用了未在 `slots` 中定义的时段 ID |

```json
{
  "errors": [
    {
      "type": "unknown_slot_reference",
      "manuscript_id": null,
      "slot_id": "s404",
      "reviewer_id": "r1",
      "detail": "评审人 r1 的可出席时段 s404 未在候选时段中定义，可用时段只能引用已声明的时段 ID"
    }
  ]
}
```

### 示例调用

```bash
curl -s http://localhost:8000/health

curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
        "required_reviewers": 2,
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"]},
          {"id": "m2", "authors": ["bob"],   "topics": ["db"]}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 2},
          {"id": "r2", "topics": ["ml", "db"], "capacity": 2},
          {"id": "r3", "topics": ["db"], "capacity": 1}
        ]
      }'

# 增量重排：锁定 m1->r1，并在全局可行前提下最少改动地复用上一轮分配
curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
        "required_reviewers": 1,
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"], "required_reviewers": 2},
          {"id": "m2", "authors": ["bob"],   "topics": ["db"]}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 2},
          {"id": "r2", "topics": ["ml", "db"], "capacity": 1},
          {"id": "r3", "topics": ["db"], "capacity": 1}
        ],
        "previous_assignments": [
          {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2"]},
          {"manuscript_id": "m2", "reviewer_ids": ["r2"]}
        ],
        "locked_assignments": [
          {"manuscript_id": "m1", "reviewer_ids": ["r1"]}
        ]
      }'

# 分赛道容量：r1 的 poster 赛道限额 1，与总容量 3 同时生效
curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
        "required_reviewers": 1,
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"], "track": "poster"},
          {"id": "m2", "authors": ["bob"],   "topics": ["ml"], "track": "poster"},
          {"id": "m3", "authors": ["carol"], "topics": ["ml"], "track": "oral"}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 3, "track_capacities": {"poster": 1}},
          {"id": "r2", "topics": ["ml"], "capacity": 1}
        ]
      }'

# 负载均衡：最小化最大负载率（返回约分分数），再最小化相对上一轮的改动数
curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
        "balance_workload": true,
        "manuscripts": [
          {"id": "m1", "authors": [], "topics": ["t"], "required_reviewers": 1},
          {"id": "m2", "authors": [], "topics": ["t"], "required_reviewers": 1}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["t"], "capacity": 2},
          {"id": "r2", "topics": ["t"], "capacity": 2}
        ],
        "previous_assignments": [
          {"manuscript_id": "m1", "reviewer_ids": ["r1"]},
          {"manuscript_id": "m2", "reviewer_ids": ["r1"]}
        ]
      }'
# -> assignments 为 m1->r1、m2->r2（接受 1 新增 1 移除，把最大负载率从 1 压到 1/2）；
#    balance = {"max_load_ratio": {"numerator": 1, "denominator": 2}, "changes": 2}

# 评审人下限覆盖：每人至少 1 篇，全局带下界流一次求解
curl -s -X POST http://localhost:8000/assign-coverage \
  -H 'Content-Type: application/json' \
  -d '{
        "required_reviewers": 1,
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"], "track": "poster"},
          {"id": "m2", "authors": ["bob"],   "topics": ["ml"], "track": "poster"},
          {"id": "m3", "authors": ["carol"], "topics": ["db"]}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 2, "min_assignments": 1,
           "track_capacities": {"poster": 1}},
          {"id": "r2", "topics": ["ml", "db"], "capacity": 2, "min_assignments": 1},
          {"id": "r3", "topics": ["db"], "capacity": 2, "min_assignments": 1}
        ]
      }'
# -> assignments 为 m1->r2、m2->r1、m3->r3（r1 的 poster 限额 1 与各人下限同时满足）

# 分角色分配：全部角色一次全局求解，同一评审人对同一稿件最多承担一个角色
curl -s -X POST http://localhost:8000/assign-roles \
  -H 'Content-Type: application/json' \
  -d '{
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"],
           "role_requirements": {"chair": 1, "reviewer": 1}},
          {"id": "m2", "authors": ["bob"], "topics": ["db"], "track": "poster",
           "role_requirements": {"reviewer": 2}}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 2, "roles": ["chair", "reviewer"]},
          {"id": "r2", "topics": ["ml", "db"], "capacity": 2, "roles": ["reviewer"]},
          {"id": "r3", "topics": ["db"], "capacity": 2, "roles": ["reviewer"],
           "track_capacities": {"poster": 1}}
        ]
      }'
# -> assignments 按稿件、角色分组：m1 的 chair->r1、reviewer->r2（r1 不能身兼两角），
#    m2 的 reviewer->r2、r3（r3 的 poster 赛道限额 1 与总容量同时生效）

# 资格审计：自稿 / 显式回避 / 主题规则之外，合作链不超过 max_hops 跳也算利益冲突
curl -s -X POST http://localhost:8000/conflict-audit \
  -H 'Content-Type: application/json' \
  -d '{
        "max_hops": 2,
        "manuscripts": [
          {"id": "m1", "authors": ["alice"], "topics": ["ml"]},
          {"id": "m2", "authors": ["bob"], "topics": ["db"]}
        ],
        "reviewers": [
          {"id": "r1", "topics": ["ml"], "capacity": 2},
          {"id": "r2", "topics": ["nlp"], "capacity": 1,
           "conflicted_manuscripts": ["m1"]},
          {"id": "r3", "topics": ["ml"], "capacity": 1}
        ],
        "collaboration_edges": [
          ["r3", "x"], ["x", "alice"]
        ]
      }'
# -> m1 的合格评审人为 r1；r2 显式回避 m1 且主题不匹配；
#    r3 经 2 跳合作链 r3 -> x -> alice 关联作者，collaboration_path 给出最短路径；
#    m2 无任何评审人主题匹配（r1/r2/r3 均仅 topic_mismatch=true，reasons 为空）

# 评审工时钟：按 UTC 事件重放，仅工作日 09:00-17:00 计工时，节假日与周末顺延
curl -s -X POST http://localhost:8000/review-clock \
  -H 'Content-Type: application/json' \
  -d '{
        "pairs": [
          {"manuscript_id": "m1", "reviewer_id": "r1", "budget_hours": 10}
        ],
        "events": [
          {"manuscript_id": "m1", "reviewer_id": "r1", "type": "accept",
           "at": "2026-09-21T10:00:00Z"},
          {"manuscript_id": "m1", "reviewer_id": "r1", "type": "pause",
           "at": "2026-09-21T12:00:00Z"},
          {"manuscript_id": "m1", "reviewer_id": "r1", "type": "resume",
           "at": "2026-09-22T10:00:00Z"}
        ],
        "holidays": ["2026-09-23"],
        "query_at": "2026-09-22T11:00:00Z"
      }'
# -> status=active，worked_hours=3（周一 2h + 周二 1h，暂停与夜间不计）；
#    恢复时剩 8h，周二计 7h 后跨节假日（周三）顺延，deadline=2026-09-24T10:00:00Z

# 共识排序：Schulze 最强路径，部分票只对同票出现的两稿计支持，平局按稿件 ID 破同
curl -s -X POST http://localhost:8000/decision-ranking \
  -H 'Content-Type: application/json' \
  -d '{
        "assignments": [
          {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2", "r3"]},
          {"manuscript_id": "m2", "reviewer_ids": ["r1", "r2", "r3"]},
          {"manuscript_id": "m3", "reviewer_ids": ["r1", "r2", "r3"]}
        ],
        "ballots": [
          {"reviewer_id": "r1", "ranking": ["m1", "m2"]},
          {"reviewer_id": "r2", "ranking": ["m2"]},
          {"reviewer_id": "r3", "ranking": ["m3", "m1"]}
        ]
      }'
# -> ranking = ["m3", "m1", "m2"]（m3>m1 与 m1>m2 各 1 票；m2 与 m3 从未同票，
#    双向支持均为 0）；pair_support 给出全部有序稿件对的直接支持数

# 讨论时段排期：全体评审人共同可出席、同评审人同时段一稿、时段容量不超出，
# 多个完整方案按稿件 ID 顺序逐位比较时段 ID，取字典序最小
curl -s -X POST http://localhost:8000/schedule-discussions \
  -H 'Content-Type: application/json' \
  -d '{
        "assignments": [
          {"manuscript_id": "m1", "reviewer_ids": ["r1", "r2"]},
          {"manuscript_id": "m2", "reviewer_ids": ["r2", "r3"]}
        ],
        "slots": [
          {"id": "s1", "capacity": 1},
          {"id": "s2", "capacity": 1}
        ],
        "reviewer_availability": [
          {"reviewer_id": "r1", "slot_ids": ["s1"]},
          {"reviewer_id": "r2", "slot_ids": ["s1", "s2"]},
          {"reviewer_id": "r3", "slot_ids": ["s2"]}
        ]
      }'
# -> status=feasible；schedule = m1->s1（r1 只能 s1）、m2->s2（r3 只能 s2）
# 无完整方案时 status=infeasible、schedule=null（不输出部分排期）；
# 超 8 篇/8 时段、重复稿件/时段 ID、评审人未声明可用时段、引用未知时段 -> 422
```

## 算法与确定性

- 建图：`源 -> 稿件（容量=所需评审数）-> 评审人（容量=1，仅合格）-> 汇（容量=评审容量）`；
  最大流等于总需求即存在满足全部约束的分配，取其流即为解。
- 分赛道容量：稿件赛道出现在评审人 `track_capacities` 中时，稿件 -> 评审人 的边改经
  「(评审人, 赛道)」限额节点（`稿件 -> (评审人,赛道) -> 评审人`，中间边容量=赛道限额），
  赛道限额与总容量在同一流网络中联立求解；未列入映射的赛道仍走直边，只受总容量约束。
- 全局求解保证整体可行（逐稿贪心无法保证，例如 m1 可选 r1/r2、m2 仅可选 r1 且容量均为 1 时，
  贪心会失败而全局解为 m1→r2、m2→r1）。
- 增量重排：锁定关系先逐一校验稿件需求、评审资格、总容量与分赛道限额（任一冲突即整体
  不可行，返回结构化 `locked_conflicts`），合法锁定对固化后扣减稿件剩余需求、评审剩余
  总容量与剩余赛道限额；其余名额用最小费用最大流（SSP + 势函数）求解——复用
  `previous_assignments` 的边费用为 0、新增边费用为 1，流量固定时费用最小即复用最多，
  亦即新增 + 移除关系总数最少；复用边同样流经赛道限额节点，历史关系仅在两级容量
  均允许时复用。
- 不可行证明：求解结束后在残量网络上从源点 BFS 得到源侧集合，跨割的正向边必然全部
  饱和；由最大流最小割定理，按 `demand` / `eligibility` / `track_capacity` /
  `reviewer_capacity` 分类列出的跨割边容量之和即割容量 = `max_assignable`，
  `shortfall` 为总需求与之差。增量模式下该割建在扣除合法锁定占用后的残量网络上，
  `max_assignable` 再加上已固化的锁定名额；锁定校验失败时不给该证明。
- 负载均衡（`balance_workload`）：完整可行解按字典序优化——先最小化所有
  `capacity > 0` 评审人最终负载率（`分配数 / capacity`）的最大值，再最小化相对
  `previous_assignments` 的新增 + 移除总数，同成本解按 ID 破除。负载率全程用
  约分分数（整数分子/分母）表示与比较，不引入浮点：最优值必为某评审人
  `最终负载 / capacity` 的分数，故先在 `1/max_cap` 整数网格上二分定位，再在区间内
  至多 `max_cap` 个精确分数候选中二分（每次判定为一次 Dinic 最大流可行性检查，
  评审人容量按 `floor(负载率 × capacity)` 收紧），最后用收紧容量下的最小费用
  最大流完成后两级优化；无完整解时沿用普通 / 增量模式的诊断，不输出部分分配。
- 分角色分配（`/assign-roles`）：`源 -> (稿件,角色)（容量=该角色所需人数）->
  (稿件,评审人)（容量=1，仅当评审人合格且可担任该角色）-> 评审人/(评审人,赛道)
  （容量=1）-> 评审人（赛道限额）-> 汇（总容量）`。(稿件,评审人) 去重节点的出边
  容量为 1，保证同一评审人对同一稿件最多承担一个角色；全部角色共享评审人总容量
  与赛道限额，在同一流网络中一次求解（Dinic 最大流），避免按角色分别分配时争用
  共享容量；不可行时按 (稿件, 角色) 输出缺口，不输出部分分配。
- 评审人下限覆盖（`/assign-coverage`）：带下界流（可行循环流）一次全局求解——
  `源 -> 稿件（[需求, 需求]）-> 评审人/(评审人,赛道)（[0,1]，仅合格）-> 评审人
  （[0, 赛道限额]）-> 汇（[下限, 容量]）-> 源（[0, 总需求] 回边）`；带下界边按
  标准变换拆为「容量-下界」边与节点供需（差额接超级源/超级汇），超级源出边全部
  饱和即存在同时满足稿件需求与全部评审人上下限的完整分配（Dinic 最大流）。
  不可行时分别用「稿件侧最大流（忽略下限，与 `/assign` 同一诊断）」与
  「评审人侧最大流（只保下限）」两个松弛给出两侧诊断——由最小割的分类讨论
  （割边只能落在稿件需求侧或评审人下限侧）可知全局不可行时两侧诊断至少其一非空；
  下限 > 容量、下限 > 个人可接上限（合格稿件数经赛道限额与容量折算）属结构性冲突，
  在求解前直接判定。
- 资格审计（`/conflict-audit`）：在去重后的无向合作图上，对每个评审人做一次
  至多 `max_hops` 跳的分层 BFS（`layers[d]` 为距评审人恰为 d 跳的人员集合），
  与稿件作者集合相交的最小层即最短链跳数；等长路径按**完整 ID 序列字典序**
  选取——自评审人起逐跳选取「仍能在剩余跳数内到达某个最短距离作者」的最小 ID
  邻点（反向分层集合保证所选邻点确实可达目标层）。合作图只由边集合决定：
  重复边、反向边与自环一律忽略，全部节点与列表均按 ID 排序处理，相同业务输入
  （含不同列表顺序）产生逐字节相同的审计结果。审计接口无状态、不涉及任何流量
  求解或分配结果，`capacity` 等容量字段不参与资格判定。
- 工时钟重放（`/review-clock`）：每对配对独立维护
  `pending → active ⇄ paused → submitted` 状态机，事件按 `(at, 输入下标)`
  稳定排序后仅重放 `at <= query_at` 者；工时以整数秒累计——计时段
  （accept/resume 到 pause/submit 或 query_at）与工作日工作时段
  （周一至周五 UTC 09:00—17:00，扣除 `holidays` 整日）逐日求区间交集，
  非工作时间与暂停期间一律不计时；预计截止时刻在每次进入计时段时由
  「该段起点 + 当时剩余工时」逐日扫描工作时段投影得到（非工作起点先钳制到其后
  第一个工作瞬间），故暂停清空截止、恢复重新投影，且计时中查询时刻推进不会
  移动截止时刻；提交后工时、截止时刻与逾期判定冻结在提交时刻。重复配对、
  未知配对、同对同刻事件与非法状态转换收集为确定性排序的问题清单整体拒绝；
  计算全程整数秒、无时区与浮点歧义，相同事件集合（含乱序输入）结果完全相同。
- 共识排序（`/decision-ranking`）：先把 `/assign` 名单去重为「评审人 -> 被分稿件
  集合」，逐票做模式校验（每张票都完整校验：每人一票、票内无重复、每稿属于其分配
  名单），问题按 `(type, reviewer_id, manuscript_id)` 收集排序后整体拒绝（422），
  不输出排序结果。通过校验后按票的严格序累加直接成对支持 `d[i][j]`（仅两稿同票
  出现时靠前者得一票，故部分票天然不给未同票比较的两稿任何支持）；路径强度矩阵 `p`
  初始仅在 `d[i][j] > d[j][i]`（成对胜者）时置为 `d[i][j]`，其余为 0（不存在边），
  再以 Floyd-Warshall 对全部 `(k,i,j)` 三元组做最宽路径松弛
  `p[i][j] = max_k min(p[i][k], p[k][j])`（不跳过 `i/j` 相同的三元组，否则会漏掉
  合法链式最强路径）。严格偏好 `p[i][j] > p[j][i]` 构成无环有向图：部分票下「无差异」
  关系可能不传递，故最终顺序不是逐对比较器排序，而是在该 DAG 上用 **min-heap
  Kahn** 取按稿件 ID 破同的拓扑序（每步在零入度稿件中输出 ID 最小者）——保持全部
  严格强于关系、势均力敌者由 ID 确定破同；全票时与通常 Schulze 排序一致。成对支持
  只取决于票集合，稿件、票、评审人列表任意重排以及名单/票内重复均产生逐字节相同的
  结果（全程整数运算）。
- 讨论时段排期（`/schedule-discussions`）：稿件与时段先按 ID 排序，回溯逐篇（稿件
  ID 升序）枚举按时段 ID 升序的可行时段，第一个完整解即「按稿件 ID 顺序逐位比较
  时段 ID」的字典序最小方案，不依赖任何随机或输入顺序。每步落位后对剩余稿件做前向
  可行性剪枝（松弛掉剩余稿件彼此之间的评审人争用，仅保留出席交集、与已排前缀的争用、
  时段剩余容量——松弛问题可行是真实问题可行的必要条件，不错杀完整解）：①每稿至少有
  一个可去时段；②时段侧 Hall 条件（邻接时段剩余容量之和 ≥ 稿件数）；③评审人侧
  Hall 条件（每位评审人在每个可出席时段是一个容量 1 的右点：凡含该评审人且可行时段
  全落在某时段集合 T 内的稿件，数量不超过 T 内该评审人仍可服务的时段数）；④带容量
  Kuhn 增广路（右侧按时段容量复制）存在覆盖全部剩余稿件的匹配。时段至多 8 个，
  ②③均以时段位掩码 + 子集和 DP 实现（单表至多 256 项），全程整数位运算；
  非法输入（超 8 篇/8 时段、重复稿件/时段 ID、重复可用时段声明、评审人未声明、
  引用未知时段）一次性收集为按 `(type, manuscript_id, slot_id, reviewer_id)`
  确定性排序的问题清单整体拒绝（422），不输出排期。
- 所有节点按 ID 排序后建图，BFS / Dijkstra 均按固定邻接顺序与 (费用, 节点下标) 字典序
  执行，算法无随机性：相同输入（含不同的列表顺序）产生逐字节相同的输出，
  同成本解由 ID 顺序确定性地破除（瓶颈割的节点与边同样按业务 ID 确定排序）。

## 本地开发与测试

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                 # 运行测试
uvicorn app.main:app --reload --port 8000   # 本地起服务
```

## 项目结构

```
app/
  main.py      # FastAPI 入口：/health、/assign、/assign-coverage、/assign-roles、/conflict-audit、/review-clock、/decision-ranking、/schedule-discussions
  models.py    # 请求/响应模型与校验
  solver.py    # 分配求解器：普通模式全局最大流 + 增量模式最小费用最大流 + 负载均衡字典序优化 + 下限覆盖带下界流 + 分角色全局分配
  audit.py     # 资格审计：自稿/显式回避/主题匹配 + 无向合作图最短链（BFS 分层 + 完整 ID 序列字典序破同）
  clock.py     # 工时钟重放：工作日历（工作日 09:00-17:00 UTC、节假日排除）整数秒计时 + 状态机重放与截止时刻投影
  ranking.py   # 共识排序：直接成对支持 + Schulze 最强路径（仅胜者边，Floyd-Warshall 最宽路径）+ 路径强度/ID 破同
  schedule.py  # 讨论时段排期：稿件/时段 ID 排序回溯求字典序最小完整解 + 容量/评审人席位 Hall 剪枝（位掩码子集 DP）+ 带容量 Kuhn
tests/
  test_assign.py                 # /assign 端到端测试
  test_assign_coverage.py        # /assign-coverage 端到端测试
  test_assign_roles.py           # /assign-roles 端到端测试
  test_conflict_audit.py         # /conflict-audit 端到端测试
  test_review_clock.py          # /review-clock 端到端测试
  test_decision_ranking.py       # /decision-ranking 端到端测试
  test_schedule_discussions.py  # /schedule-discussions 端到端测试
Dockerfile
docker-compose.yml
requirements.txt
requirements-dev.txt
```
