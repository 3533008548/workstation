# 契约二 · 任务与 Run 执行契约

> 状态：草案 v0.1.0 · 单一事实来源 `contracts/python/workstation_contracts/run.py`、`skill.py`

---

## 1. 为什么需要统一 Run

工作台要同时驱动三种**运行时形态完全不同**的东西：

| 技能 | 形态 | 现状 |
|---|---|---|
| `research.*` | Python 进程内，LangGraph 三节点（llm / tools / verify） | 有 checkpoint 依赖（`langgraph-checkpoint-sqlite` 已在 requirements） |
| `knowledge.*` | **长驻本地 HTTP 服务**（TS 桌面端） | 目前是 Electron 应用，需要暴露服务接口 |
| `pptx.*` | **一次性子进程**（Node CLI） | ⚠️ `pptxgenjs` 有模块级状态串扰，同进程并发会产出多余图表文件 |

如果每种形态一套进度协议，前端、预算治理、审计日志就得写三遍。**Run 是这三种形态的唯一公共外壳**：LangGraph 调用、子进程渲染、HTTP 桥接，都产出同一个 Run 形状。

---

## 2. 生命周期状态机

```
                 ┌────────────────► cancelled
                 │
  pending ──► running ──► waiting ──┐
                 │                  │
                 ├──────────────────┼──► succeeded
                 ├──────────────────┼──► partial      (部分步骤已产出，整体未完成)
                 └──────────────────┴──► failed
```

- `waiting`：阻塞于外部输入 / 人工审批 / 限流队列。这是 `require_approval` 与预算排队落点。
- `partial`：与 `failed` 分开是刻意的——长任务（深度研究、批量生成）中途挂掉时，**已经产出的 artifact 仍然有效**，不该被整体判死。
- 终态不可重开：`transition()` 对终态抛 `ValueError`。

---

## 3. 类型清单

### TaskRequest → Run

```
TaskRequest{ task_id, skill, inputs, options, parent_run_id, requested_at }
Run{ run_id, task_id, skill, skill_version, status, inputs, outputs,
     options, steps[], artifacts[], usage, source_ids[],
     parent_run_id, checkpoint_ref, idempotency_key, 时间戳族 }
```

- `idempotency_key`：重复提交同一 key 返回既有 Run。长任务重试必需。
- `parent_run_id`：子任务链（深度研究 → 检索 → 生成 Deck）。
- `checkpoint_ref`：**跨形态的恢复句柄**——LangGraph thread id / 子进程 workdir / 不透明 token。这让"跨进程可恢复"成为契约的一部分，而不是某个框架的特性。

#### checkpoint_ref 由谁写、怎么用

字段定义完之后很长一段时间里它是**死字段**（全项目只有定义、零写入）。运行时层落地后，写入方按形态分工：

| 形态 | 写入方 | 句柄内容 | 恢复方式 |
|---|---|---|---|
| `subprocess` | 技能（PPT）在 `mkdir(workdir)` 之后立即写入 | `derived/runs/<run_id>/` | 目录里的 `request.json` 完整描述这次渲染，原位重放 |
| `http` | 桥接服务首次响应回传 | 服务端下发的不透明 token | 轮询 / 续跑 |
| `local` | 编排框架（LangGraph） | thread id | `graph.invoke(..., config={"thread_id": ...})` |

两条约束：

1. **必须是幂等的**——同一个 `checkpoint_ref` 重复恢复，结果必须等价。
2. **不是所有技能都可恢复**。`SkillRuntime.resume()` 只对实现了 `resume` 的技能生效，其余抛 `NotImplementedError`。这是有意的不对称：一次性渲染（PPT）重跑比恢复更便宜也更安全，长流程（科研编排）才必须能续跑。

### TaskOptions

| 字段 | 默认 | 说明 |
|---|---|---|
| `priority` | `normal` | `interactive / normal / batch`。**对应科研助手已有的 interactive reserved slots 模型**，交互式请求不被批处理饿死 |
| `timeout_s` | null | ≥1 |
| `budget` | 空 | 见下 |
| `idempotency_key` | null | |
| `dry_run` | false | |
| `require_approval` | **false** | true → 任何 `fs:primary` / `vault:write` 副作用前阻塞 |
| `locale` | `zh-CN` | |

### Budget：硬上限，超限即失败

`max_tokens / max_cost_cny / max_tool_calls / max_wall_clock_s / max_llm_calls`

`budget_breach()` 返回第一个被突破的上限名。**永不静默超支**——超限是 Run 失败，不是警告。

> 这条直接解决架构梳理里发现的问题：三个项目都调 DeepSeek/GLM，却各有各的预算与限流，跨项目零协调。预算统一到 Run 层之后，才能做全局治理。

### Step / Usage

`Step{ step_id, name, kind, status, 时间戳, usage, message, error }`，`kind ∈ llm/tool/retrieve/render/verify/write`。

`Usage{ prompt_tokens, completion_tokens, tool_calls, llm_calls, cost_cny, wall_clock_s, retries }`，可 `add()` 累加；`Run.recompute_usage()` 从步骤重算总量。

### Artifact

`Artifact{ kind, uri, media_type, origin, producer, source_ids[], fact_ids[] }`。

**`source_ids` / `fact_ids` 是关键**：产出物自带溯源，Deck 的每一页能反查到是哪几条事实、哪几个来源。这是 PPT "数字溯源硬约束"在契约层的落点。

### RunEvent：前端唯一的 SSE 契约

```
event: <type>
id: <seq>
data: <单行 JSON>
```

- **data 强制单行**——多行 `data:` 会击穿朴素的 SSE 解析器（已由测试锁定）。
- `type ∈ run.created / run.started / run.progress / run.completed / run.failed / run.cancelled / step.started / step.finished / artifact.produced / budget.warning / approval.required`
- `seq` 单调递增，供断线重连补齐。
- `run_id` 形状强校验（必须 `run_*`）。

现有科研助手前端已经在做 SSE 与任务取消，这份契约是**把它固化**，不是新增。

---

## 4. SkillManifest：技能如何自声明

```yaml
name: pptx                    # ^[a-z][a-z0-9-]{1,63}$
version: 0.1.0                # 严格 major.minor.patch
description: ...
when_to_use: ...              # 路由读这个，不是 description
runtime:
  kind: subprocess            # inprocess | subprocess | http | bridge
  entrypoint: node cli.js
  isolated: true              # ← PPT 必须：pptxgenjs 模块级状态串扰
permissions:
  - { resource: fs:primary, requires_confirm: true }
default_budget: { max_cost_cny: 2.0 }
```

**`runtime.kind` 是整个跨语言策略的核心：**

| kind | 用于 | 理由 |
|---|---|---|
| `inprocess` | 科研技能 | 直接 import，零 IPC 开销 |
| `subprocess` | **PPT** | 进程隔离是**硬约束**，不是优化。`isolated: true` 强制每次 Run 起新进程 |
| `http` | 知识资产层 | TS 侧长驻服务，底座只依赖接口 |
| `bridge` | 未定形态 | 适配器自管传输，底座不关心 |

### 与 SKILL.md 的关系

`name / version / description / when_to_use` 保持与社区 **SKILL.md** frontmatter 兼容，只额外加 `runtime` + `permissions` 两块。

代价：要按它的规范写描述与触发条件。
收益：白捡一份成熟格式与整个社区技能生态，**且不受主栈选择影响**——这是全案最划算的一次生态对齐。

### 权限枚举

`llm / net / fs:primary / fs:derived / subprocess / vault:write`

`vault:write` 单独列出，是因为知识资产层是**唯一写入通道**（见契约三）。任何技能想写 Vault 都必须声明它。

---

## 5. 已落地与未落地

| 能力 | 状态 |
|---|---|
| 模型定义 + 校验 + 状态机 | ✅ 已实现，28 项测试通过 |
| JSON Schema 导出 | ✅ `contracts/schema/*.schema.json`（8 份） |
| TS 镜像类型 | ✅ `contracts/ts/src/index.ts` |
| SSE 帧序列化 | ✅ `RunEvent.to_sse()` |
| 预算治理器（全局限流/配额） | ⬜ 阶段 1，随模型网关落地 |
| Run 持久化（SQLite） | ⬜ 阶段 1 |
| LangGraph / 子进程 / HTTP 三种执行器 | ⬜ 阶段 1–3 |
